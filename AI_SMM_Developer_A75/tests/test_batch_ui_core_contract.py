"""Qt-free exercise of the real GUI batch entrypoint and durable runtime."""
import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime

ROOT=Path(__file__).resolve().parents[1]

def batch_method(name):
    tree=ast.parse((ROOT/'app.py').read_text(encoding='utf8'))
    funcs=[n for c in tree.body if isinstance(c,ast.ClassDef)
           for n in c.body if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(funcs)==1
    space={'sqlite3':sqlite3,'Path':Path,'now':lambda:'timestamp',
           'QTimer':SimpleNamespace(singleShot=lambda delay,fn:fn())}
    exec(compile(ast.fix_missing_locations(ast.Module(body=funcs,type_ignores=[])),str(ROOT/'app.py'),'exec'),space)
    return space[name]

class BatchUiCoreContract(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);self.root=root/'p';(self.root/'originals').mkdir(parents=True)
        (self.root/'processed').mkdir()
        self.source=self.root/'originals'/'image.png';self.source.write_bytes(b'input')
        self.db=open_application_database(root/'db.sqlite',root);self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO projects(id,name,created_at) VALUES ('p','P','now')");self.db.commit()
        self.core=ApplicationRuntime(self.db,lambda pid:self.root)
        self.run=self.core.batches.create('p',[str(self.source)],'edit',0)
        self.log=[]
        self.view=SimpleNamespace(current_project='p',batch_active=True,batch_queue=[str(self.source)],
            batch_owner_project='p',batch_run_id=self.run.batch_id,batch_total=1,
            batch_prompt='edit',batch_claim=None,batch_inflight=None,
            source=SimpleNamespace(show_file=lambda path:self.log.append(('source',path))),
            core=self.core,modern_worker=None,cloud_worker=None,engine_mode=0,
            modern_local_generate=lambda *a,**k:self.log.append(('start',a)),
            append_chat=lambda *a:self.log.append(('chat',a)))

    def test_launch_that_does_not_create_worker_cannot_remove_receipt(self):
        batch_method('batch_next')(self.view)
        self.assertFalse(self.view.batch_active)
        row=self.db.execute("SELECT status,attempt_token FROM batch_item_receipts WHERE batch_id=?",(self.run.batch_id,)).fetchone()
        self.assertEqual(row,('failed',None))
        self.assertEqual(self.core.batches.pending('p').paths,(str(self.source),))
        self.assertFalse(self.db.in_transaction)

    def test_real_worker_start_claims_but_does_not_mark_completed(self):
        self.view.modern_local_generate=lambda *a,**k:setattr(self.view,'modern_worker',object())
        batch_method('batch_next')(self.view)
        self.assertTrue(self.view.batch_active)
        self.assertEqual(self.view.batch_queue,[])
        self.assertIsNotNone(self.view.batch_claim)
        self.assertEqual(self.db.execute('SELECT status FROM batch_item_receipts').fetchone(),('running',))
        with self.assertRaises(ValueError):self.core.batches.finish('p',self.run.batch_id)

if __name__=='__main__':unittest.main()
