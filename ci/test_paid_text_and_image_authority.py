"""Shared cap and exact request grants across actual text/image dispatch code.

All provider HTTP is mocked. These tests never authorize a live model call.
"""
import base64
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from durable_cost_gate import DurableCostGate
from paid_text_runtime import reserve_text,operation_for,text_payment_scope
from text_providers import call_text
from cost_authorization import PaymentDenied
from PIL import Image


class PaidTextAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.gate=DurableCostGate(Path(self.temp.name)/'ledger.sqlite')
        self.model='openai/test-model'
        self.messages=[{'role':'user','content':'Full project task'}]
        self.env={'OPENAI_API_KEY':'offline-fixture'}

    def authorization(self,maximum='1',**options):
        operation=operation_for('owner','task',self.model,self.messages,**options)
        token=self.gate.approve('openai',operation,maximum)
        return reserve_text(self.gate,'owner','task',self.model,self.messages,maximum,
                             approval_token=token,**options)

    def test_direct_free_provider_requires_account_free_tier_confirmation(self):
        for model,key in [('groq/openai/gpt-oss-20b','GROQ_API_KEY'),
                          ('gemini/gemini-3.1-flash-lite','GEMINI_API_KEY')]:
            with patch('text_providers._chat_request') as network:
                with self.assertRaises(PaymentDenied):call_text(model,self.messages,environ={key:'fixture'})
            network.assert_not_called()

    def test_key_and_environment_flags_do_not_authorize_payment(self):
        with patch('text_providers._chat_request') as http:
            with self.assertRaises(PaymentDenied):call_text(self.model,self.messages,environ=self.env)
        http.assert_not_called();self.assertEqual(self.gate.usage()['committed_usd'],0)

    def test_timeout_keeps_shared_reservation_and_cannot_replay_after_restart(self):
        payment=self.authorization()
        http=Mock(side_effect=TimeoutError('fixture'))
        with patch('text_providers._chat_request',http):
            with self.assertRaises(TimeoutError):call_text(self.model,self.messages,environ=self.env,payment=payment)
        restarted=DurableCostGate(self.gate.database_path)
        from dataclasses import replace
        payment=replace(payment,gate=restarted)
        with patch('text_providers._chat_request') as next_http:
            with self.assertRaises(PaymentDenied):call_text(self.model,self.messages,environ=self.env,payment=payment)
        next_http.assert_not_called();http.assert_called_once()
        self.assertEqual(restarted.usage()['pending_usd'],1)

    def test_exact_model_prompt_and_options_are_bound_to_approval(self):
        payment=self.authorization()
        with patch('text_providers._chat_request') as http:
            for args in ({'messages':[{'role':'user','content':'Different task'}]},
                         {'temperature':.9},{'response_format':'json_object'},{'model':'openai/another-model'}):
                values={'model':self.model,'messages':self.messages,'environ':self.env,'payment':payment,**args}
                with self.assertRaises(PaymentDenied):call_text(**values)
        http.assert_not_called()
        with patch('text_providers._chat_request',return_value=('Saved answer',{})):
            result=call_text(self.model,self.messages,environ=self.env,payment=payment)
        self.assertEqual(result['message']['content'],'Saved answer')
        self.assertEqual(self.gate.usage()['pending_usd'],1)

    def test_text_and_media_share_one_twenty_dollar_budget(self):
        self.gate.set_monthly_cap('1')
        payment=self.authorization()
        token=self.gate.approve('kie.ai','media:fixture','.1')
        with self.assertRaises(PaymentDenied):self.gate.reserve('kie.ai','media:fixture','.1',token=token)
        self.assertEqual(self.gate.usage()['available_usd'],0)

    def test_competing_grants_cannot_reserve_the_same_exact_operation_twice(self):
        operation=operation_for('owner','task',self.model,self.messages)
        tokens=[self.gate.approve('openai',operation,'1') for _ in range(2)]
        barrier=threading.Barrier(2);results=[];failures=[]
        def reserve(token):
            barrier.wait(timeout=5)
            try:results.append(reserve_text(self.gate,'owner','task',self.model,self.messages,'1',approval_token=token))
            except PaymentDenied:failures.append('denied')
        workers=[threading.Thread(target=reserve,args=(token,)) for token in tokens]
        for worker in workers:worker.start()
        for worker in workers:worker.join(5);self.assertFalse(worker.is_alive())
        self.assertEqual(len(results),1);self.assertEqual(failures,['denied'])
        self.assertEqual(self.gate.usage()['pending_usd'],1)

    def test_payment_scope_does_not_escape_into_another_thread(self):
        payment=self.authorization();failures=[]
        def other_thread():
            try:call_text(self.model,self.messages,environ=self.env)
            except PaymentDenied:failures.append('denied')
        with patch('text_providers._chat_request',return_value=('OK',{})) as http:
            with text_payment_scope(payment):
                worker=threading.Thread(target=other_thread);worker.start();worker.join(5)
                self.assertFalse(worker.is_alive())
                call_text(self.model,self.messages,environ=self.env)
            with self.assertRaises(PaymentDenied):call_text(self.model,self.messages,environ=self.env)
        self.assertEqual(failures,['denied']);http.assert_called_once()


class LegacyImageGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source.png'
        with Image.new('RGB',(64,64)) as image:image.save(self.source,'PNG')
        self.gate=DurableCostGate(self.root/'ledger.sqlite')

    def worker(self,authorized):
        from app import CloudEditWorker
        from paid_image_runtime import image_operation
        worker=CloudEditWorker(self.source,None,self.root/'output.png','Campaign','fixture',use_reference=False)
        if authorized:
            operation=image_operation('owner','task',self.source,None,'Campaign')
            token=self.gate.approve('openai',operation,'.5')
            reservation=self.gate.reserve('openai',operation,'.5',token=token)
            worker.cost_gate=self.gate;worker.payment_binding=('owner','task',operation,reservation)
        return worker

    def test_legacy_image_cannot_bypass_budget_by_direct_worker_construction(self):
        worker=self.worker(False);errors=[];worker.failed.connect(errors.append)
        with patch('urllib.request.urlopen') as http:worker.run()
        http.assert_not_called();self.assertEqual(len(errors),1)

    def test_authorized_image_dispatch_is_single_use_and_preserves_reservation(self):
        worker=self.worker(True);errors=[];success=[]
        worker.failed.connect(errors.append);worker.done.connect(success.append)
        data=base64.b64encode(self.source.read_bytes()).decode()
        http=Mock(side_effect=lambda *a,**kw:io.BytesIO(json.dumps({'data':[{'b64_json':data}]}).encode()))
        with patch('urllib.request.urlopen',http):worker.run();worker.run()
        http.assert_called_once();self.assertEqual(len(success),1);self.assertEqual(len(errors),1)
        self.assertTrue(Path(success[0]).is_file());self.assertEqual(str(self.gate.usage()['pending_usd']),'0.5')

    def test_changed_input_is_rejected_before_paid_transport(self):
        worker=self.worker(True);errors=[];worker.failed.connect(errors.append)
        with Image.new('RGB',(64,64),(50,60,70)) as image:image.save(self.source,'PNG')
        with patch('urllib.request.urlopen') as http:worker.run()
        http.assert_not_called();self.assertEqual(len(errors),1)

if __name__=='__main__':unittest.main()
