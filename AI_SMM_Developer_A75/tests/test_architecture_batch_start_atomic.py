"""Batch start is a single durable project operation, not a UI-only flag."""
import ast, json, sqlite3, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime
ROOT=Path(__file__).resolve().parents[1]

def start_batch(bindings):
 tree=ast.parse((ROOT/'app.py').read_text('utf8'))
 funcs=[n for cls in tree.body if isinstance(cls,ast.ClassDef)
   for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='start_batch']
 assert len(funcs)==1
 ns=dict(bindings)
 exec(compile(ast.fix_missing_locations(ast.Module(body=funcs,type_ignores=[])),str(ROOT/'app.py'),'exec'),ns)
 return ns['start_batch']

class BatchStartAtomic(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
  self.root=Path(t.name)/'project';(self.root/'originals').mkdir(parents=True)
  self.source=self.root/'originals'/'image.png';self.source.write_bytes(b'image')
  self.db=open_application_database(self.root.parent/'manager.sqlite',self.root.parent);self.addCleanup(self.db.close)
  self.db.execute("INSERT INTO projects(id,name,created_at) VALUES('p','P','now')");self.db.commit()
  self.core=ApplicationRuntime(self.db,lambda pid:self.root)
  self.messages=[];self.events=[]
  self.view=SimpleNamespace(batch_active=False,modern_worker=None,cloud_worker=None,current_project='p',db=self.db,
   source_list=SimpleNamespace(selectedItems=lambda:['a'],row=lambda x:0),working_sources=[str(self.source)],
   reference=SimpleNamespace(path=None),message=SimpleNamespace(toPlainText=lambda:'Make a careful edit',clear=lambda:self.events.append('clear')),
   append_chat=lambda *a:self.messages.append(a),engine_mode=0, batch_queue=['previous'],batch_run_id='previous',
   batch_next=lambda:self.events.append('run'))
  self.view.core=self.core
  box=SimpleNamespace(question=lambda *a:1,Yes=1,No=2)
  self.env={'QMessageBox':box, 'Path':Path, 'IMAGE_EXTENSIONS':{'.png'},'same_file':lambda a,b:False,
    'get_openai_key':lambda:'', 'now':lambda:'now','uuid4':uuid4,'json':json,'sqlite3':sqlite3,
    'ensure_project_folders':lambda pid:self.root}

 def test_failed_sql_insert_does_not_start_worker_or_change_existing_queue(self):
  self.db.execute('CREATE TRIGGER abort_start BEFORE INSERT ON batch_runs BEGIN SELECT RAISE(ABORT,"fault injected");END')
  start_batch(self.env)(self.view)
  self.assertFalse(self.db.in_transaction)
  self.assertFalse(self.view.batch_active)
  self.assertEqual(self.view.batch_queue,['previous'])
  self.assertEqual(self.view.batch_run_id,'previous')
  self.assertEqual(self.events,[])
  self.assertIn('не запущена',str(self.messages))
  self.assertEqual(self.db.execute('SELECT COUNT(*) FROM batch_runs').fetchone()[0],0)

 def test_success_persists_queue_before_starting_worker(self):
  def run():
   row=self.db.execute('SELECT queue_json,status FROM batch_runs').fetchone()
   self.assertEqual(json.loads(row[0]),[str(self.source)]);
   self.assertEqual(row[1],'running');self.assertFalse(self.db.in_transaction)
   self.events.append('run')
  self.view.batch_next=run
  start_batch(self.env)(self.view)
  self.assertTrue(self.view.batch_active)
  self.assertEqual(self.view.batch_queue,[str(self.source)])
  self.assertEqual(self.events,['clear','run'])

 def test_foreign_transaction_does_not_start_worker_or_commit(self):
  self.db.execute('CREATE TABLE foreign_write(v TEXT)');self.db.execute('INSERT INTO foreign_write VALUES (?)',('other',))
  start_batch(self.env)(self.view)
  self.assertTrue(self.db.in_transaction)
  self.assertFalse(self.view.batch_active)
  self.assertEqual(self.db.execute('SELECT v FROM foreign_write').fetchone()[0],'other')
  self.db.rollback()

 def test_linked_original_folder_cannot_become_batch_input(self):
  outside=self.root.parent/'external';outside.mkdir();f=outside/'image.png';f.write_bytes(b'leave alone')
  self.source.unlink();(self.root/'originals').rmdir()
  try:(self.root/'originals').symlink_to(outside,target_is_directory=True)
  except OSError as exc:
   if getattr(exc,'winerror',None)==1314:self.skipTest('Windows symlink permission missing')
   raise
  start_batch(self.env)(self.view)
  self.assertFalse(self.view.batch_active)
  self.assertEqual(self.db.execute('SELECT COUNT(*) FROM batch_runs').fetchone()[0],0)
  self.assertEqual(f.read_bytes(),b'leave alone')

if __name__=='__main__':unittest.main()
