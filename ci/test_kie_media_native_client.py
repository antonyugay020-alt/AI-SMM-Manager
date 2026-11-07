"""Documented native HTTP contracts; offline fixtures, no Kie charges."""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from kie_media_client import KieMediaClient, _NoRedirect
from kie_media_contracts import nano_banana_pro
from durable_cost_gate import DurableCostGate

TASK='task_native_1234567890'

class NativeClientTests(unittest.TestCase):
    def response(self, value):
        return io.BytesIO(json.dumps(value).encode())

    def test_query_exact_documented_authenticated_route(self):
        sent=[]
        result={'code':200,'data':{'taskId':TASK,'model':'nano-banana-pro','state':'waiting'}}
        def http(req,timeout):
            sent.append(req)
            return self.response(result)
        client=KieMediaClient(api_key='secret',opener=http)
        self.assertEqual(client.query_task(TASK),result)
        self.assertEqual(sent[0].full_url,'https://api.kie.ai/api/v1/jobs/recordInfo?taskId='+TASK)
        self.assertEqual(sent[0].get_header('Authorization'),'Bearer secret')
        self.assertEqual(sent[0].method,'GET')
        self.assertIsNone(sent[0].data)

    def test_query_rejects_invalid_identity_state_and_provider_failure(self):
        for patch in ({'taskId':'other_task_1234567890'},{'model':'unknown'}, {'state':'invented'}):
            data={'taskId':TASK,'model':'nano-banana-pro','state':'waiting',**patch}
            client=KieMediaClient(api_key='secret',opener=lambda *a,**kw:self.response({'code':200,'data':data}))
            with self.assertRaises(ValueError):
                client.query_task(TASK)
        http=Mock()
        client=KieMediaClient(api_key='secret',opener=http)
        for ident in ('x?secret=1','',None):
            with self.assertRaises(ValueError):client.query_task(ident)
        http.assert_not_called()

    def test_upload_multipart_stream_is_bounded_and_requires_separate_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'private-customer-name.mp4'
            content=b'abcd'*(1024*1024)
            path.write_bytes(content)
            calls=[]
            def http(req,timeout):
                chunks=list(req.data)
                body=b''.join(chunks)
                self.assertLessEqual(max(map(len,chunks)),1024*1024)
                self.assertEqual(len(body),int(req.get_header('Content-length')))
                self.assertIn(content,body)
                self.assertIn(b'name="uploadPath"',body)
                self.assertIn(b'name="fileName"',body)
                self.assertNotIn(path.name.encode(),body)
                self.assertEqual(req.full_url,'https://kieai.redpandaai.co/api/file-stream-upload')
                calls.append(req)
                return self.response({'code':200,'success':True,'data':{'fileSize':len(content),
                    'downloadUrl':'https://tempfile.redpandaai.co/owned.mp4'}})
            client=KieMediaClient(api_key='secret',opener=http)
            for consent in (False,1,'yes'):
                with self.assertRaises(RuntimeError):
                    client.upload_file(path,explicit_upload_consent=consent)
            self.assertFalse(calls)
            self.assertEqual(client.upload_file(path,explicit_upload_consent=True),
                             'https://tempfile.redpandaai.co/owned.mp4')
            self.assertEqual(len(calls),1)

    def test_upload_rejects_bad_receipt_and_private_result_url(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'photo.png'
            path.write_bytes(b'bytes validated by gateway')
            for data in ({'fileSize':1,'downloadUrl':'https://example.com/a.png'},
                         {'fileSize':path.stat().st_size,'downloadUrl':'https://127.0.0.1/a.png'}):
                client=KieMediaClient(api_key='secret',opener=lambda *a,**kw:self.response(
                    {'code':200,'success':True,'data':data}))
                with self.assertRaises(ValueError):
                    client.upload_file(path,explicit_upload_consent=True)

    def test_transport_failure_is_bounded_and_does_not_expose_secrets(self):
        for failure in (urllib.error.URLError('secret'),TimeoutError('secret'),
                        urllib.error.HTTPError('https://secret',403,'secret',{},None)):
            client=KieMediaClient(api_key='secret',opener=Mock(side_effect=failure))
            with self.assertRaises(RuntimeError) as raised: client.query_task(TASK)
            self.assertNotIn('secret',str(raised.exception))
            client._opener.assert_called_once()
        for body in (b'{secret',b'x'*120001,b'[]',b'{"code":401,"msg":"secret"}'):
            client=KieMediaClient(api_key='secret',opener=lambda *a,**kw:io.BytesIO(body))
            with self.assertRaises(RuntimeError) as raised:client.query_task(TASK)
            self.assertNotIn('secret',str(raised.exception))

    def test_authenticated_redirect_is_never_followed(self):
        with self.assertRaises(RuntimeError):
            _NoRedirect().redirect_request(None,None,302,'redirect',{},'https://attacker.example')

    def test_native_gateway_full_upload_dispatch_and_result_with_real_files(self):
        import sqlite3
        from PIL import Image
        from application_paid_media import PaidMediaComposition
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            owner='a'*32
            db=sqlite3.connect(root/'project.sqlite')
            self.addCleanup(db.close)
            db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
            db.execute('INSERT INTO projects VALUES(?)',(owner,))
            db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,stored_path TEXT)')
            db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
            source=root/owner/'originals'/'photo.png'
            source.parent.mkdir(parents=True)
            with Image.new('RGB',(320,320)) as image:image.save(source,'PNG')
            db.execute('INSERT INTO assets VALUES(?,?,?)',('b'*32,owner,str(source)))
            db.commit()
            calls=[]
            def http(req,timeout):
                calls.append(req.full_url)
                if req.full_url.endswith('file-stream-upload'):
                    body=b''.join(req.data)
                    self.assertIn(source.read_bytes(),body)
                    return self.response({'code':200,'success':True,'data':{
                        'fileSize':source.stat().st_size,'downloadUrl':'https://tempfile.redpandaai.co/photo.png'}})
                if req.full_url.endswith('createTask'):
                    self.assertEqual(json.loads(req.data)['input']['image_input'],
                                     ['https://tempfile.redpandaai.co/photo.png'])
                    return self.response({'code':200,'data':{'taskId':TASK}})
                self.assertIn('recordInfo?taskId=',req.full_url)
                return self.response({'code':200,'data':{'taskId':TASK,'model':'nano-banana-pro',
                    'state':'success','resultJson':json.dumps({'resultUrls':['https://example.org/result.png']})}})
            def download(_,dest):
                with Image.new('RGB',(128,96),(10,20,30)) as image:image.save(dest,'PNG')
            composition=PaidMediaComposition(db,lambda pid:root/pid,root/'ledger.sqlite')
            gateway=composition.native_gateway(verified_price_quote=lambda *_:'0.25',
                api_key='secret',enable_paid=True,opener=http,fetch_verified_file=download)
            self.assertEqual(calls,[])
            job=gateway.prepare_image(owner,prompt='Campaign',sources=[source],explicit_upload_consent=True)
            token=composition.cost_gate.approve('kie.ai','media:'+job,'0.25')
            self.assertEqual(gateway.start(owner,job,explicit_paid_consent=True,approval_token=token),TASK)
            result=gateway.refresh(owner,job)
            self.assertTrue(Path(result).is_file())
            self.assertEqual(len(calls),3)
            self.assertEqual(gateway.refresh(owner,job),result)
            self.assertEqual(len(calls),3)
            with self.assertRaises(RuntimeError):
                gateway.start(owner,job,explicit_paid_consent=True,approval_token=token)
            self.assertEqual(str(composition.cost_gate.usage()['pending_usd']),'0.25')
            self.assertEqual(db.execute('SELECT project_id FROM project_files').fetchone()[0],owner)

    def test_invalid_response_consumes_paid_dispatch_claim_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            gate=DurableCostGate(Path(directory)/'ledger.sqlite')
            token=gate.approve('kie.ai','media:one','1')
            reservation=gate.reserve('kie.ai','media:one','1',token=token)
            http=Mock(side_effect=lambda *a,**kw:io.BytesIO(b'{invalid'))
            client=KieMediaClient(api_key='secret',enabled=True,opener=http)
            args=dict(cost_gate=gate,payment_reservation=reservation,operation='media:one')
            with self.assertRaises(RuntimeError):client.create_task(nano_banana_pro('Cover',[]),**args)
            with self.assertRaises(PermissionError):client.create_task(nano_banana_pro('Cover',[]),**args)
            http.assert_called_once()
            self.assertEqual(gate.usage()['pending_usd'],1)

if __name__=='__main__':unittest.main()
