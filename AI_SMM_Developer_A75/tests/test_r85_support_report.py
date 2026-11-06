"""Self-contained evidence, redaction, capture and atomic project snapshots."""
import io,json,os,sqlite3,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import diagnostic_report as dr
from ollama_resilience import request_chat,OllamaStageError

class SupportReport(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.workspace=self.root/'project';self.workspace.mkdir();self.studio=self.root/'studio';(self.studio/'logs').mkdir(parents=True)
  self.source=self.root/'source';self.source.mkdir();self.db=sqlite3.connect(':memory:')
  self.db.executescript('CREATE TABLE projects(id TEXT,name TEXT); CREATE TABLE manager_documents(id TEXT,project_id TEXT,kind TEXT,title TEXT,content TEXT); CREATE TABLE messages(project_id TEXT,role TEXT,content TEXT); CREATE TABLE ai_pending_tasks(project_id TEXT,request TEXT,question TEXT); CREATE TABLE future_project_data(project_id TEXT,payload TEXT); CREATE TABLE credentials(project_id TEXT,api_key TEXT);')
  self.db.executemany('INSERT INTO projects VALUES(?,?)',[('one','Project one'),('other','Other')])
  self.db.executemany('INSERT INTO manager_documents VALUES(?,?,?,?,?)',[('doc','one','calendar','Calendar','FULL_GENERATED_CONTENT'),('private','other','strategy','Other','NEVER_EXPORT_OTHER_PROJECT')])
  self.db.execute('INSERT INTO messages VALUES(?,?,?)',('one','user','EXACT ORIGINAL TASK'))
  self.db.execute('INSERT INTO ai_pending_tasks VALUES(?,?,?)',('one','EXACT ORIGINAL TASK','Not completed'))
  self.db.execute('INSERT INTO future_project_data VALUES(?,?)',('one','future evidence'))
  self.db.execute('INSERT INTO credentials VALUES(?,?)',('one','NEVER_EXPORT_CREDENTIAL_ROW'))
  self.key='sk-proj-'+'example_secret_85'*3
 def tearDown(self):self.db.close();self.temp.cleanup()
 def build(self,**kw):
  snap=dr.snapshot_project(self.db,'one',{'editor_content':'UNSAVED_EDITOR_CONTENT'})
  with patch.object(dr,'ollama_inventory',return_value={'status':'test_injected'}):r=dr.build_report(snap,self.workspace,self.studio,self.source,self.root/'report.zip',**kw)
  return r,zipfile.ZipFile(r['path'])
 def test_snapshot_is_project_scoped_and_retains_new_project_tables(self):
  snap=dr.snapshot_project(self.db,'one');self.assertEqual(snap['tables']['future_project_data'][0]['payload'],'future evidence')
  self.assertNotIn('credentials',snap['tables']);self.assertNotIn('NEVER_EXPORT_OTHER_PROJECT',json.dumps(snap))
  with self.assertRaises(ValueError):dr.snapshot_project(self.db,'absent')
 def test_full_content_pending_editor_history_partial_logs_and_checksums(self):
  d=self.workspace/'drafts';d.mkdir();(d/'progress.json').write_text('{"posts":["draft-one","draft-two"],"completed":0}', encoding='utf-8')
  (d/'failure.jsonl').write_text('{"error":"full failure"}\nPARTIAL_LAST_LINE', encoding='utf-8')
  r,z=self.build()
  with z:
   combined='\n'.join(z.read(n).decode() for n in z.namelist())
   for v in ('FULL_GENERATED_CONTENT','UNSAVED_EDITOR_CONTENT','EXACT ORIGINAL TASK','draft-one','draft-two','PARTIAL_LAST_LINE','full failure'):self.assertIn(v,combined)
   self.assertNotIn('NEVER_EXPORT_OTHER_PROJECT',combined);self.assertNotIn('NEVER_EXPORT_CREDENTIAL_ROW',combined)
   import hashlib
   for n,v in json.loads(z.read('SUMMARY.json'))['files'].items():self.assertEqual(hashlib.sha256(z.read(n)).hexdigest(),v['sha256'])
  self.assertGreater(r['files'],5)
 def test_secret_redaction_preserves_source_and_usage(self):
  original=json.dumps({'api_key':self.key,'nested':{'access_token':'unrecognized-secret-value'},'usage':{'total_tokens':985}})
  (self.workspace/'settings.json').write_text(original, encoding='utf-8')
  (self.studio/'logs'/'console.log').write_text('Authorization: Bearer '+self.key+'\nOPENAI_API_KEY='+self.key, encoding='utf-8')
  (self.source/'fixture.py').write_text('value = "'+self.key+'"', encoding='utf-8')
  r,z=self.build(environ={'OPENAI_API_KEY':self.key,'AI_SMM_MODEL':'local:8b'})
  with z:
   combined=''.join(z.read(n).decode() for n in z.namelist());self.assertNotIn(self.key,combined);self.assertNotIn('unrecognized-secret-value',combined);self.assertIn('985',combined)
   self.assertTrue(json.loads(z.read('runtime/environment.json'))['key_availability']['OPENAI_API_KEY'])
  self.assertEqual((self.workspace/'settings.json').read_text(encoding='utf-8'),original)
 def test_media_credentials_and_links_are_inventoried_without_following(self):
  (self.workspace/'clip.mp4').write_bytes(b'binary payload');(self.workspace/'.env').write_text('SECRET=DO_NOT_INCLUDE_ENV_FILE', encoding='utf-8')
  (self.root/'external.txt').write_text('DO_NOT_FOLLOW_LINK', encoding='utf-8')
  try:(self.workspace/'outside.txt').symlink_to(self.root/'external.txt')
  except OSError:pass
  r,z=self.build()
  with z:
   self.assertTrue(any(i['path']=='clip.mp4' and not i['included'] for i in json.loads(z.read('FILE_INVENTORY.json'))))
   combined=''.join(z.read(n).decode() for n in z.namelist());self.assertNotIn('DO_NOT_INCLUDE_ENV_FILE',combined);self.assertNotIn('DO_NOT_FOLLOW_LINK',combined)
 def test_atomic_failure_keeps_previous_archive(self):
  dest=self.root/'report.zip';dest.write_bytes(b'previous archive')
  with patch.object(dr,'ollama_inventory',return_value={}),patch.object(dr.os,'replace',side_effect=OSError('write failed')):
   with self.assertRaises(OSError):dr.build_report(dr.snapshot_project(self.db,'one'),self.workspace,self.studio,self.source,dest)
  self.assertEqual(dest.read_bytes(),b'previous archive');self.assertFalse(list(self.root.glob('.smm_report_*.tmp')))
 def test_missing_evidence_is_explicit(self):
  r,z=self.build()
  with z:
   self.assertEqual(json.loads(z.read('installation/source_integrity.json'))['status'],'UNVERIFIED');self.assertIn('no_application_trace',z.read('SUMMARY.json').decode())
 def test_model_trace_contains_full_prompt_schema_response_and_usage(self):
  p={'model':'fixture:8b','stream':False,'format':'json','messages':[{'role':'user','content':'FULL_MODEL_TASK '+self.key}]}
  response={'message':{'content':'{"answer":"FULL_MODEL_RESPONSE"}'},'eval_count':42,'usage':{'total_tokens':99}}
  with patch.dict(os.environ,{'OPENAI_API_KEY':self.key}),dr.capture_task(self.workspace,'EXACT TASK'),patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(response).encode())):
   out=request_chat(p,stage='full trace',attempts=1)
  self.assertEqual(out['message']['content'],response['message']['content']);log=(self.workspace/'drafts/application_events.jsonl').read_text(encoding='utf-8')
  self.assertNotIn(self.key,log)
  for v in ('FULL_MODEL_TASK','FULL_MODEL_RESPONSE','99','model_transport_body','model_call_completed'):self.assertIn(v,log)
 def test_bad_transport_and_exception_include_raw_body_and_traceback(self):
  with self.assertRaises(OllamaStageError),dr.capture_task(self.workspace,'Failed task'),patch('urllib.request.urlopen',return_value=io.BytesIO(b'RAW_BAD_TRANSPORT_BODY')):
   request_chat({'model':'fixture','messages':[]},stage='bad transport',attempts=1)
  log=(self.workspace/'drafts/application_events.jsonl').read_text(encoding='utf-8')
  for v in ('RAW_BAD_TRANSPORT_BODY','Traceback','task_exception'):self.assertIn(v,log)
 def test_full_http_error_is_captured_beyond_the_bounded_user_message(self):
  import urllib.error
  from ollama_resilience import ProviderHTTPError
  body=b'X'*12000+b'COMPLETE_HTTP_ERROR_TAIL'
  error=urllib.error.HTTPError('http://localhost',500,'failed',{},io.BytesIO(body))
  with self.assertRaises(ProviderHTTPError) as caught,dr.capture_task(self.workspace,'HTTP failure'),patch('urllib.request.urlopen',side_effect=error):
   request_chat({'model':'fixture','messages':[]},stage='HTTP failure',attempts=1)
  self.assertLessEqual(len(caught.exception.response_text),8192)
  self.assertIn('COMPLETE_HTTP_ERROR_TAIL',(self.workspace/'drafts/application_events.jsonl').read_text(encoding='utf-8'))
 def test_logging_failure_cannot_change_model_output(self):
  blocked=self.root/'file';blocked.write_text('not a directory', encoding='utf-8');response={'message':{'content':'unchanged response'}}
  with dr.capture_task(blocked,'task'),patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(response).encode())):
   out=request_chat({'model':'fixture','messages':[]},stage='no logging',attempts=1)
  self.assertEqual(out['message']['content'],'unchanged response')
 def test_cloud_raw_reply_cannot_record_provider_credential(self):
  from text_providers import call_text
  response={'choices':[{'message':{'content':'Reply '+self.key}}],'usage':{'total_tokens':17}}
  with dr.capture_task(self.workspace,'Cloud task'),patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(response).encode())):
   call_text('openai/gpt-6-luna',[],environ={'OPENAI_API_KEY':self.key})
  log=(self.workspace/'drafts/application_events.jsonl').read_text(encoding='utf-8');self.assertNotIn(self.key,log);self.assertNotIn('Reply ',log);self.assertIn('cloud_transport_metadata',log);self.assertNotIn('cloud_transport_body',log)
 def test_transient_error_details_survive_successful_retry_without_changing_policy(self):
  response=io.BytesIO(json.dumps({'message':{'content':'successful retry'}}).encode())
  with dr.capture_task(self.workspace,'Retry task'),patch('urllib.request.urlopen',side_effect=[TimeoutError('TRANSIENT_FULL_DETAIL'),response]) as wire,patch('ollama_resilience.time.sleep'):
   result=request_chat({'model':'fixture','messages':[]},stage='retry',attempts=2)
  self.assertEqual(wire.call_count,2);self.assertEqual(result['message']['content'],'successful retry')
  self.assertIn('TRANSIENT_FULL_DETAIL',(self.workspace/'drafts/application_events.jsonl').read_text(encoding='utf-8'))
 def test_directory_walk_errors_are_explicit(self):
  def denied(root,**kw):
   kw['onerror'](PermissionError('EXPLICIT_DIRECTORY_READ_FAILURE'))
   return iter(())
  with patch.object(dr.os,'walk',side_effect=denied):
   result,z=self.build()
  with z:self.assertIn('EXPLICIT_DIRECTORY_READ_FAILURE',z.read('SUMMARY.json').decode())
 def test_redaction_inside_strings_does_not_corrupt_json_documents_or_jsonl(self):
  self.db.execute('INSERT INTO messages VALUES(?,?,?)',('one','user','An embedded credential: api_key=unrecognized_value and another password="hidden_value".'))
  (self.workspace/'strings.json').write_text(json.dumps({'message':'Configure api_key=unrecognized_value before using it.'}), encoding='utf-8')
  (self.workspace/'events.jsonl').write_text(json.dumps({'message':'password="hidden_value"'})+'\n', encoding='utf-8')
  result,z=self.build()
  with z:
   for name in z.namelist():
    text=z.read(name).decode()
    if name.endswith('.json'):json.loads(text)
    if name.endswith('.jsonl'):
     for line in text.splitlines():json.loads(line)
    self.assertNotIn('unrecognized_value',text);self.assertNotIn('hidden_value',text)
 def test_windows_legacy_text_is_included_with_encoding(self):
  (self.workspace/'legacy.log').write_bytes('Полная ошибка Windows'.encode('cp1251'));r,z=self.build()
  with z:
   self.assertIn('Полная ошибка Windows',z.read('workspace/legacy.log').decode());self.assertTrue(any(i.get('encoding')=='cp1251' for i in json.loads(z.read('FILE_INVENTORY.json'))))

if __name__=='__main__':unittest.main()
