"""Loopback HTTP and admission contracts, never live-model quality evidence."""
import copy
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
import local_text_runtime as local
from model_roles import model_family,authors_with_review_capacity

MODEL='lmstudio/qwen/qwen2.5-14b-instruct'
MODEL_ID=MODEL.removeprefix('lmstudio/')
ENV={'AI_SMM_LOCAL_LLM_BACKEND':'lm_studio','AI_SMM_LOCAL_LLM_MODEL':MODEL,
     'AI_SMM_LOCAL_LLM_URL':'http://127.0.0.1:1234'}
ENTRY={'id':MODEL_ID,'type':'llm','state':'loaded','arch':'qwen2','quantization':'Q4_K_M'}
PAYLOAD={'model':MODEL,'messages':[{'role':'user','content':'Проверь: ёлка, кедр и янтарь.'}],
         'format':{'type':'object','properties':{'ответ':{'type':'string'}},'required':['ответ'],
                   'additionalProperties':False},'options':{'num_predict':256,'temperature':0}}


def response(model=MODEL_ID,content='{"ответ":"Кириллица сохранена: ёлка"}',finish='stop'):
    return {'model':model,'choices':[{'finish_reason':finish,'message':{'role':'assistant','content':content}}]}


class LMStudioContracts(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.addCleanup(self.env.stop)

    def test_real_loopback_utf8_schema_and_single_completion_through_shared_gateway(self):
        from ollama_resilience import request_chat,capture_utf8_transport
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                seen.append(('GET',self.path,None))
                self.reply({'data':[ENTRY]})
            def do_POST(self):
                raw=self.rfile.read(int(self.headers['Content-Length']))
                seen.append(('POST',self.path,raw))
                self.reply(response())
            def reply(self,data):
                raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            env={**ENV,'AI_SMM_LOCAL_LLM_URL':f'http://127.0.0.1:{server.server_port}',
                 'AI_SMM_QA_LOCAL_ONLY':'1','AI_SMM_QA_ALLOWED_MODELS':json.dumps([MODEL]),
                 'HTTP_PROXY':'http://127.0.0.1:9'}
            with patch.dict(os.environ,env),patch('text_providers.call_text') as cloud, \
                 patch('urllib.request.urlopen') as ollama,capture_utf8_transport() as evidence:
                result=request_chat(PAYLOAD,stage='loopback-contract',attempts=1,timeout=10)
            cloud.assert_not_called();ollama.assert_not_called()
            self.assertEqual([row[:2] for row in seen],[('GET','/api/v0/models'),('POST','/v1/chat/completions')])
            body=json.loads(seen[1][2]);self.assertEqual(body['model'],MODEL_ID)
            self.assertEqual(body['response_format']['json_schema']['schema'],PAYLOAD['format'])
            self.assertEqual(body['response_format']['type'],'json_schema')
            self.assertNotIn('chat_template_kwargs',body)
            self.assertIn('ёлка',seen[1][2].decode('utf-8'))
            self.assertEqual(evidence['request_utf8'],seen[1][2].decode('utf-8'))
            self.assertTrue(evidence['response_utf8_valid'])
            self.assertIn('ёлка',result['message']['content'])
        finally:
            server.shutdown();thread.join(timeout=5);server.server_close()

    def test_unloaded_duplicate_embedding_remote_missing_inventory_never_generates(self):
        for entries in ([],[ENTRY,ENTRY],[{**ENTRY,'state':'not-loaded'}],
                        [{**ENTRY,'type':'embeddings'}],[{**ENTRY,'remote_host':'remote.invalid'}]):
            wire=Mock(return_value=io.BytesIO(json.dumps({'data':entries}).encode()))
            with self.assertRaises(ValueError):local.request_local(PAYLOAD,environ=ENV,opener=wire)
            self.assertEqual(wire.call_count,1);self.assertEqual(wire.call_args.args[0].get_method(),'GET')

    def test_default_port_and_inventory_share_the_same_request_deadline(self):
        env={key:value for key,value in ENV.items() if key!='AI_SMM_LOCAL_LLM_URL'}
        self.assertEqual(local.configuration_identity(MODEL,environ=env)['endpoint'],
                         'http://127.0.0.1:1234/v1/chat/completions')
        with patch('local_text_runtime.loaded_model') as inventory, \
             patch('local_text_runtime.time.monotonic',side_effect=[10,21]):
            with self.assertRaises(TimeoutError):local.request_local(PAYLOAD,environ=env,timeout=10,opener=Mock())
        inventory.assert_called_once()
        for timeout in (0,-1,float('nan'),float('inf'),True):
            with patch('local_text_runtime.loaded_model') as inventory:
                with self.assertRaises(ValueError):local.request_local(PAYLOAD,environ=env,timeout=timeout)
                inventory.assert_not_called()

    def test_response_identity_truncation_and_invalid_utf8_fail_closed(self):
        for result in (response(model='other'),response(finish='length'),response(content='')):
            wire=Mock(side_effect=[io.BytesIO(json.dumps({'data':[ENTRY]}).encode()),
                                  io.BytesIO(json.dumps(result).encode())])
            with self.assertRaises(ValueError):local.request_local(PAYLOAD,environ=ENV,opener=wire)
            self.assertEqual(wire.call_count,2)
        from ollama_resilience import capture_utf8_transport
        wire=Mock(side_effect=[io.BytesIO(json.dumps({'data':[ENTRY]}).encode()),io.BytesIO(b'\xff')])
        with capture_utf8_transport() as evidence:
            with self.assertRaises(UnicodeDecodeError):local.request_local(PAYLOAD,environ=ENV,opener=wire,evidence=evidence)
        self.assertFalse(evidence['response_utf8_valid'])

    def test_cancel_before_inventory_and_unconfigured_route_have_no_fallback(self):
        from cooperative_cancellation import TaskCancelled,cancellation_scope
        from ollama_resilience import request_chat
        wire=Mock()
        def cancelled():raise TaskCancelled('test cancellation')
        with self.assertRaises(TaskCancelled),cancellation_scope(cancelled):
            local.request_local(PAYLOAD,environ=ENV,opener=wire)
        wire.assert_not_called()
        with patch('urllib.request.urlopen') as ollama,patch('text_providers.call_text') as cloud:
            with self.assertRaises(ValueError):request_chat(PAYLOAD,stage='no-fallback',attempts=1)
        ollama.assert_not_called();cloud.assert_not_called()

    def test_roster_requires_explicit_selection_and_allowlist(self):
        from generic_text_review import list_installed_text_models
        with patch('local_text_runtime.loaded_model',return_value=ENTRY) as probe, \
             patch('generic_text_review.list_installed_ollama_models',return_value=['qwen2.5-coder:7b']):
            self.assertEqual(list_installed_text_models(),['qwen2.5-coder:7b']);probe.assert_not_called()
            with patch.dict(os.environ,ENV):self.assertEqual(list_installed_text_models(),['qwen2.5-coder:7b',MODEL])
            with patch.dict(os.environ,{**ENV,'AI_SMM_QA_LOCAL_ONLY':'1','AI_SMM_QA_ALLOWED_MODELS':'[]'}):
                with self.assertRaises(ValueError):list_installed_text_models()

    def test_size_provider_quantization_and_derivatives_do_not_invent_independence(self):
        for model in (MODEL,'qwen2.5:14b','qwen2.5-coder:7b','lmstudio/publisher/Qwen2.5-7B-Instruct-Q4_K_M.gguf'):
            self.assertEqual(model_family(model),'qwen2.5')
        self.assertEqual(model_family('lmstudio/vendor/qwen3.5-14b-a3b-claude-distilled'),'qwen3.5')
        self.assertEqual(model_family('lmstudio/meta-llama-3.1-8b-instruct'),model_family('llama3.1:8b'))
        self.assertEqual(model_family('lmstudio/google/gemma-3-4b-it-Q4_K_M.gguf'),model_family('gemma3:4b'))
        self.assertEqual(authors_with_review_capacity([MODEL],['qwen2.5-coder:7b','llama3.1:8b'])[0],[])

    def test_qualification_cache_and_child_certificate_bound_to_local_route(self):
        import review_qualification as qualification
        # Existing known-answer test fixture; this proves wiring, never model quality.
        import importlib.util
        spec=importlib.util.spec_from_file_location('lm_qualification_fixture',ROOT/'AI_SMM_Developer_A75/tests/qualification_fixture.py')
        fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
        with patch.dict(os.environ,ENV),patch.dict(qualification._CACHE,{},clear=True):
            good=qualification.probe_qualification(MODEL,seed=6901,transport=lambda payload,**kw:fixture.qualification_reply(payload),force=True)
            self.assertTrue(good['pass']);self.assertIsNotNone(qualification.cached_qualification(MODEL))
            envelope=json.dumps(qualification.qualification_envelope([good]))
            for change in ({'AI_SMM_LOCAL_LLM_URL':'http://127.0.0.1:1235'},
                           {'AI_SMM_LOCAL_LLM_SHA256':'b'*64},{'AI_SMM_LOCAL_LLM_BACKEND':''}):
                with patch.dict(os.environ,{**change,qualification.CERT_ENV:envelope}):
                    self.assertIsNone(qualification.cached_qualification(MODEL))

    def test_qa_weight_binding_scrubs_inherited_authority_and_detects_file_change(self):
        import windows_qa_session as session
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'локальная модель.gguf';path.write_bytes(b'GGUFfixture-a')
            config={'local_text':{'backend':'lm_studio','model':MODEL,'url':ENV['AI_SMM_LOCAL_LLM_URL'],'model_file':str(path)}}
            with patch('local_text_runtime.loaded_model',return_value=ENTRY):
                env,first=session.local_text_environment(config)
                path.write_bytes(b'GGUFfixture-b')
                _,second=session.local_text_environment(config)
            self.assertNotEqual(first['weights_sha256'],second['weights_sha256'])
            self.assertEqual(env['AI_SMM_LOCAL_LLM_SHA256'],first['weights_sha256'])
            path.write_bytes(b'not a GGUF')
            with self.assertRaises(ValueError):session.local_text_environment(config)
        with patch('local_text_runtime.loaded_model') as probe:
            self.assertEqual(session.local_text_environment({}),({},None));probe.assert_not_called()

    def test_saved_selection_is_reused_without_wizard_or_automatic_load(self):
        import windows_qa_session as session
        config={'local_text':{'backend':'lm_studio','model':MODEL}}
        with patch('local_text_runtime.lm_studio_inventory') as network:
            self.assertIs(session.configure_local_text(Path('unused.json'),config),config)
        network.assert_not_called()

    def test_inventory_http_failure_closes_response_and_does_not_generate(self):
        from urllib.error import HTTPError
        raw=io.BytesIO(b'local server error')
        failure=HTTPError('http://localhost:1234/api/v0/models',401,'auth',{},raw)
        wire=Mock(side_effect=failure)
        with self.assertRaises(local.ProviderHTTPError):local.request_local(PAYLOAD,environ=ENV,opener=wire)
        self.assertTrue(raw.closed);self.assertEqual(wire.call_count,1)

    def test_one_time_model_selection_preserves_existing_media_settings(self):
        from types import SimpleNamespace
        import windows_qa_session as session
        config={'profiles':'unchanged-profiles','plan':'unchanged-plan','vision_model':'gemma3:4b'}
        ui=Mock();dialogs=SimpleNamespace(askstring=Mock(return_value=ENV['AI_SMM_LOCAL_LLM_URL']),askinteger=Mock(return_value=1))
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);weights=root/'qwen.gguf';weights.write_bytes(b'GGUFfixture')
            module=SimpleNamespace(Tk=lambda:ui,simpledialog=dialogs,
                messagebox=SimpleNamespace(askyesno=Mock(return_value=True)),
                filedialog=SimpleNamespace(askopenfilename=Mock(return_value=str(weights))))
            with patch.object(session,'os',SimpleNamespace(name='nt',environ={})), \
                 patch.dict(sys.modules,{'tkinter':module}), \
                 patch('local_text_runtime.lm_studio_inventory',return_value=[ENTRY]), \
                 patch('local_text_runtime.loaded_model',return_value=ENTRY):
                selected=session.configure_local_text(root/'settings.json',config)
            self.assertEqual(selected['local_text']['model'],MODEL)
            self.assertEqual({key:selected[key] for key in config},config)
            self.assertEqual(json.loads((root/'settings.json').read_text()),selected)
        ui.destroy.assert_called_once()

    def test_unqualified_models_and_wrong_exit_never_advance_diagnostic_counter(self):
        from types import SimpleNamespace
        import windows_qa_session as session
        for status,code in (('PASS',0),('BLOCKED',2),('BLOCKED',0),('PASS',2)):
            with tempfile.TemporaryDirectory() as temporary:
                folder=Path(temporary);state={'clean_consecutive':8,'target_clean_consecutive':8}
                inventory=[{'name':'vision','digest':'a'*64}]
                roster={'local_text':None}
                vision=Mock();vision.call.return_value={'models':inventory}
                def child(command,env,run,timeout):
                    session.write_json(run/'report/reviewer_qualification.json',{'status':status})
                    self.assertEqual(timeout,270)
                    return code
                with patch.object(session,'os',SimpleNamespace(name='nt',environ={})), \
                     patch('local_media_quality.LocalVision',return_value=vision), \
                     patch.object(session,'prepare_model_roster',return_value=(inventory,['vision'],roster,{},None)), \
                     patch.object(session,'source_snapshot',return_value={'fingerprint':'test'}), \
                     patch.object(session,'verify_snapshot'),patch.object(session,'run_child',side_effect=child):
                    exit_code=session.execute_reviewer_diagnostic({'vision_model':'vision'},folder,state)
                passed=status=='PASS' and code==0
                self.assertEqual(exit_code,0 if passed else 2)
                self.assertEqual(state['status'],'DIAGNOSTIC_COMPLETE' if passed else 'BLOCKED')
                self.assertEqual(state['target_clean_consecutive'],0)
                self.assertFalse(state['acceptance_run']);self.assertFalse(state['release_eligible'])

    def test_inventory_reads_do_not_consume_or_add_model_calls_to_qualification(self):
        import review_qualification as qualification
        from ollama_resilience import request_chat
        sent=[]
        def wire(req,**kwargs):
            sent.append(req.get_method())
            # A syntactically valid but semantically failing answer must remain
            # a failure after exactly one completion, without a retry loop.
            data={'data':[ENTRY]} if req.get_method()=='GET' else response(content='{"decisions":{}}')
            return io.BytesIO(json.dumps(data).encode())
        with patch.dict(os.environ,ENV),patch.dict(qualification._CACHE,{},clear=True), \
             patch('local_text_runtime.request.build_opener') as builder:
            builder.return_value.open=wire
            result=qualification.probe_qualification(MODEL,seed=6901,force=True)
        self.assertFalse(result['pass']);self.assertEqual(result['model_calls'],1)
        self.assertEqual(sent,['GET','POST'])


if __name__=='__main__':unittest.main()
