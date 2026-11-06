import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import application_core as core

class ApplicationCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.a='a'*32
        self.b='b'*32
        self.db=sqlite3.connect(':memory:')
        self.db.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT);
            INSERT INTO projects VALUES ('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','A');
            INSERT INTO projects VALUES ('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','B');''')
        self.db.commit()
    def tearDown(self):
        self.db.close()
        self.temp.cleanup()
    def workspace(self,project_id):
        return self.root/project_id
    def test_persists_and_exports_text(self):
        result=core.save_project_text(self.db,self.workspace,self.a,'hello','2026')
        self.assertTrue(result.exported)
        self.assertEqual(result.path.read_text(encoding='utf-8'),'hello')
    def test_two_projects_remain_disjoint(self):
        a=core.save_project_text(self.db,self.workspace,self.a,'first','2026')
        b=core.save_project_text(self.db,self.workspace,self.b,'second','2026')
        self.assertNotEqual(a.path.parent,b.path.parent)
        self.assertEqual(a.path.read_text(encoding='utf-8'),'first')
        self.assertEqual(b.path.read_text(encoding='utf-8'),'second')
    def test_invalid_project_rolls_back_everything(self):
        with self.assertRaises(ValueError):
            core.save_project_text(self.db,self.workspace,'f'*32,'hello','2026')
        self.assertEqual(self.db.execute('SELECT count(*) FROM project_files').fetchone()[0],0)
    def test_existing_name_is_fail_closed(self):
        ident='c'*32
        first=core.save_project_text(self.db,self.workspace,self.a,'first','2026',file_id=ident)
        with self.assertRaises(sqlite3.IntegrityError):
            core.save_project_text(self.db,self.workspace,self.a,'second','2026',file_id=ident)
        self.assertEqual(first.path.read_text(encoding='utf-8'),'first')
        self.assertFalse(self.db.in_transaction)
    def test_foreign_transaction_is_not_committed(self):
        self.db.execute('INSERT INTO projects VALUES(?,?)',('c'*32,'C'))
        with self.assertRaises(RuntimeError):
            core.save_project_text(self.db,self.workspace,self.a,'test','2026')
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
    def test_export_failure_retains_pending_intent(self):
        with patch.object(core,'drain_text',return_value={'failed':1}):
            result=core.save_project_text(self.db,self.workspace,self.a,'recover','2026')
        self.assertFalse(result.exported)
        self.assertEqual(self.db.execute('SELECT state FROM project_text_projections').fetchone(),('pending',))
        core.drain_text(self.db,self.workspace,project_id=self.a)
        self.assertEqual(result.path.read_text(encoding='utf-8'),'recover')
    def test_completion_only_after_export_confirmed(self):
        self.db.execute('CREATE TABLE ai_file_effect_outbox(project_id TEXT,artifact_id TEXT,state TEXT)')
        self.db.execute('INSERT INTO ai_file_effect_outbox VALUES(?,?,?)',(self.a,'d'*32,'pending'))
        self.db.commit()
        with patch.object(core,'drain_documents') as drain, patch.object(core,'finalize_document') as finish:
            result=core.complete_ai_document(self.db,self.workspace,lambda *a:None,lambda *a:'ok',
                        project_id=self.a,artifact_id='d'*32,request='request',kind='strategy',title='title',task_id=None)
        self.assertFalse(result)
        finish.assert_not_called()
    def test_recovery_service_is_ui_independent(self):
        with patch.object(core,'recover',return_value='report') as recover:
            result=core.restore_project_results(self.db,self.workspace,lambda *a:None,lambda *a:'ok')
        self.assertEqual(result,'report')
        recover.assert_called_once()

if __name__=='__main__': unittest.main()
