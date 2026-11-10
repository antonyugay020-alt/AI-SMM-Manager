"""Actual OS exclusion across diffusion and local text/vision; no GPU claim."""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
import local_compute_lease as leases
from ollama_resilience import request_chat
from local_media_quality import LocalVision


class ComputeLeaseTests(unittest.TestCase):
    @contextmanager
    def holder(self):
        with tempfile.TemporaryDirectory() as directory:
            lock=Path(directory)/'устройство.lock'
            code="import sys;sys.path.insert(0,sys.argv[1]);from generative_runner import device_lock\nwith device_lock(sys.argv[2]):\n print('held',flush=True);sys.stdin.buffer.read()\n"
            child=subprocess.Popen([sys.executable,'-X','utf8','-c',code,str(ROOT/'AI_SMM_Work_Architecture_A75'),str(lock)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
            try:
                self.assertEqual(child.stdout.readline().strip(),'held')
                yield child,lock
            finally:
                if not child.stdin.closed:child.stdin.close()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
                child.stdout.close();child.stderr.close()

    def test_local_text_waits_for_diffusion_and_releases_its_own_residency(self):
        with self.holder() as (child,lock):
            waiting=threading.Event();entered=threading.Event();result=[];errors=[]
            real=leases.device_lease
            def lease(**kw):return real(lock,on_wait=waiting.set,**kw)
            def http(req,timeout):
                entered.set();body=json.loads(req.data)
                self.assertEqual(body['keep_alive'],0)
                return io.BytesIO(b'{"message":{"content":"ok"}}')
            def text():
                try:result.append(request_chat({'model':'local-fixture','messages':[],'keep_alive':300},stage='lease fixture',timeout=5,attempts=1))
                except BaseException as exc:errors.append(exc)
            with patch.object(leases,'device_lease',side_effect=lease),patch('urllib.request.urlopen',side_effect=http):
                thread=threading.Thread(target=text);thread.start()
                try:
                    self.assertTrue(waiting.wait(3));self.assertFalse(entered.is_set())
                    child.stdin.close();child.wait(timeout=5);thread.join(timeout=8)
                    self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
                    self.assertEqual(result[0]['message']['content'],'ok')
                finally:
                    if not child.stdin.closed:child.stdin.close()
                    thread.join(timeout=8)
            with real(lock,wait=False):pass

    def test_vision_shares_lease_but_inventory_does_not_load_or_unload_models(self):
        with self.holder() as (child,lock):
            vision=LocalVision('vision-fixture');calls=[]
            vision.http=lambda req,timeout: (calls.append(req.full_url) or io.BytesIO(b'{"models":[]}'))
            real=leases.device_lease
            with patch.object(leases,'device_lease',side_effect=lambda **kw:real(lock,max_wait_seconds=.1)):
                self.assertEqual(vision.call('/api/tags'),{'models':[]})
                with self.assertRaisesRegex(TimeoutError,'LOCAL_DEVICE_LOCK_TIMEOUT'):
                    vision.call('/api/chat',{'model':'vision-fixture','messages':[],'keep_alive':0})
            self.assertEqual(calls,['http://127.0.0.1:11434/api/tags'])
            self.assertIsNone(child.poll())

    def test_nested_owned_lease_is_reentrant_and_cancelled_wait_keeps_owner(self):
        with self.holder() as (child,lock):
            def stop():raise InterruptedError('cancelled')
            with self.assertRaises(InterruptedError):
                with leases.device_lease(lock,check=stop):pass
            self.assertIsNone(child.poll())
            child.stdin.close();child.wait(timeout=5)
            with leases.device_lease(lock,wait=False):
                with leases.device_lease(lock,wait=False):pass
            with leases.device_lease(lock,wait=False):pass
