"""Real stdlib subprocess isolation, import stalls and OS parent-death ownership."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
import native_process_runtime as native
from model_roles import model_family,authors_with_review_capacity


class NativeRuntimeLifecycle(unittest.TestCase):
    def test_isolated_child_ignores_parent_pythonpath_and_user_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);script=root/'identity.py';log=root/'child.log'
            script.write_text("import json,sys,os;print(json.dumps({'isolated':sys.flags.isolated,'user_site':sys.flags.no_user_site,'paths':sys.path,'offline':os.environ['HF_HUB_OFFLINE']}))",encoding='utf-8')
            hostile=root/'hostile';hostile.mkdir();(hostile/'sitecustomize.py').write_text("raise RuntimeError('unexpected inherited startup')")
            env={**os.environ,'PYTHONPATH':str(hostile),'PYTHONHOME':str(hostile)}
            with log.open('wb') as output,native.owned_native_process(native.native_command(sys.executable,script),env=env,stdout=output,stderr=output) as child:
                child.wait(timeout=5);self.assertEqual(child.returncode,0)
            proof=json.loads(log.read_text(encoding='utf-8'))
            self.assertEqual(proof['isolated'],1);self.assertEqual(proof['user_site'],1)
            self.assertNotIn(str(hostile),proof['paths']);self.assertEqual(proof['offline'],'1')
            self.assertEqual(env['PYTHONPATH'],str(hostile))

    def test_hanging_import_is_bounded_and_diagnostic_survives_termination(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);script=root/'stalled.py'
            script.write_text("import json,time,os;print(json.dumps({'stage':'importing_torch','pid':os.getpid()}),flush=True);time.sleep(30)")
            command=native.native_command(sys.executable,script)
            with patch.object(native,'native_command',return_value=command):
                with self.assertRaises(native.NativeRuntimeError) as failure:
                    native.probe_native_runtime(sys.executable,os.environ,timeout=.4,diagnostic_path=root/'proof.json')
            proof=json.loads((root/'proof.json').read_text())
            self.assertEqual(proof['status'],'BLOCKED')
            self.assertEqual(proof['error_code'],'NATIVE_PREFLIGHT_TIMEOUT')
            self.assertIn('importing_torch',proof['log_tail'])
            self.assertEqual(failure.exception.diagnostic,proof)

    def test_actual_parent_death_stops_child_even_when_native_code_holds_gil(self):
        import psutil
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);busy=root/'busy.py';ready=root/'ready'
            # PyDLL deliberately holds the GIL: a Python EOF thread cannot run.
            busy.write_text("import ctypes,os,sys;from pathlib import Path;Path(sys.argv[1]).write_text(str(os.getpid()))\nif os.name=='nt':ctypes.PyDLL('kernel32').Sleep(30000)\nelse:ctypes.PyDLL(None).sleep(30)\n")
            code="import sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from native_process_runtime import native_command,owned_native_process\nwith open(sys.argv[4],'wb') as log,owned_native_process(native_command(sys.executable,sys.argv[2],sys.argv[3]),env=__import__('os').environ,stdout=log,stderr=log) as child:\n deadline=time.monotonic()+5\n while not Path(sys.argv[3]).exists() and time.monotonic()<deadline:time.sleep(.02)\n print(child.pid,flush=True);time.sleep(30)\n"
            parent=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'AI_SMM_Work_Architecture_A75'),str(busy),str(ready),str(root/'child.log')],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            target=None
            try:
                launcher_pid=int(parent.stdout.readline())
                self.assertTrue(ready.is_file())
                runtime_pid=int(ready.read_text())
                # Windows venv python.exe can be a launcher/redirector which
                # starts a second interpreter process with a DIFFERENT PID.
                # Check the OS ancestry, then follow the real Python PID
                # recorded by the native workload, not the launcher PID.
                launcher=psutil.Process(launcher_pid)
                descendants={child.pid for child in launcher.children(recursive=True)}
                self.assertIn(runtime_pid,{launcher_pid,*descendants})
                target=psutil.Process(runtime_pid)
                parent.terminate();parent.wait(timeout=5)
                until=time.monotonic()+5
                while time.monotonic()<until:
                    try:
                        alive=target.is_running() and target.status()!=psutil.STATUS_ZOMBIE
                    except psutil.NoSuchProcess:
                        alive=False
                    if not alive:break
                    time.sleep(.03)
                try:
                    self.assertTrue(not target.is_running() or target.status()==psutil.STATUS_ZOMBIE)
                except psutil.NoSuchProcess:
                    pass
            finally:
                if parent.poll() is None:parent.kill();parent.wait(timeout=5)
                if target is not None:
                    try:
                        if target.is_running() and target.status()!=psutil.STATUS_ZOMBIE:target.kill()
                    except psutil.NoSuchProcess:
                        pass
                parent.stdout.close();parent.stderr.close()

    def test_qwen14b_is_supported_but_coder_derivative_does_not_create_independence(self):
        self.assertEqual(model_family('qwen2.5:14b'),model_family('qwen2.5-coder:7b'))
        self.assertEqual(model_family('qwen2.5:14b'),model_family('openrouter/qwen/qwen2.5-14b-instruct'))
        authors,deferred=authors_with_review_capacity(['qwen2.5:14b'],['qwen2.5-coder:7b','deepseek-r1:8b'])
        self.assertEqual(authors,[]);self.assertEqual(deferred[0]['available_reviewer_families'],1)
        authors,_=authors_with_review_capacity(['qwen2.5:14b'],['deepseek-r1:8b','llama3.1:8b'])
        self.assertEqual(authors,['qwen2.5:14b']) # capacity fixture, not live qualification

    def test_partial_or_contradictory_runtime_receipt_never_certifies_preflight(self):
        for proof in ({},{'cuda':True},{'cuda':False,'name':'RTX 3080 Ti'}):
            with self.assertRaises(ValueError):native.validate_runtime_receipt(proof)

    def test_runner_module_loads_no_torch_or_pillow_before_native_init(self):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'imports.py';out=Path(directory)/'result'
            script.write_text("import generative_runner,sys,json;print(json.dumps({'torch':'torch' in sys.modules,'PIL':'PIL' in sys.modules}))")
            # Use a script located beside the trusted module through explicit import path.
            script.write_text("import sys,json;sys.path.insert(0,"+repr(str(ROOT/'AI_SMM_Work_Architecture_A75'))+");import generative_runner;print(json.dumps({'torch':'torch' in sys.modules,'PIL':'PIL' in sys.modules}))")
            with out.open('wb') as log,native.owned_native_process(native.native_command(sys.executable,script),env=os.environ,stdout=log,stderr=log) as child:
                child.wait(timeout=5);self.assertEqual(child.returncode,0)
            self.assertEqual(json.loads(out.read_text()),{'torch':False,'PIL':False})

    def test_cleanup_escalates_after_terminate_race_and_never_leaks_pipe(self):
        import io
        class Process:
            stdin=io.BytesIO();returncode=None;killed=False
            def poll(self):return self.returncode
            def terminate(self):raise OSError('termination raced native exit')
            def wait(self,timeout):
                if not self.killed:raise subprocess.TimeoutExpired('owned fixture',timeout)
                return self.returncode
            def kill(self):self.killed=True;self.returncode=-9
        process=Process()
        with patch.object(native,'_create_job',return_value=None),patch.object(native,'_spawn_native',return_value=process):
            with native.owned_native_process(['owned fixture'],env={},stdout=None,stderr=None):pass
        self.assertTrue(process.killed);self.assertTrue(process.stdin.closed)

    def test_failed_coder_does_not_hide_qualified_14b_of_same_family(self):
        import generic_text_review as review
        class Ordered:
            def shuffle(self,items):pass
            def choice(self,items):return items[0]
        tags=['qwen2.5-coder:7b','qwen2.5:14b','deepseek-r1:8b']
        with patch.object(review,'configured_model_pool',return_value={'allowlist':[],'exclude':[]}),patch.object(review,'list_acceptance_models',return_value=tags),patch.object(review,'_reviewer_pool_is_prevalidated',return_value=False),patch.object(review,'_has_structured_json_contract',side_effect=lambda name:name!='qwen2.5-coder:7b') as qualify:
            assignments=review.select_reviewer_models('writer',chooser=Ordered())
        self.assertEqual({call.args[0] for call in qualify.call_args_list},set(tags))
        self.assertEqual(set(assignments.values()),{'qwen2.5:14b','deepseek-r1:8b'})
        self.assertEqual(len({model_family(tag) for tag in assignments.values()}),2)
