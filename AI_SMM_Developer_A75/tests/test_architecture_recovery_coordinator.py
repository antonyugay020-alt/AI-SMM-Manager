import json
import sqlite3
import unittest
from unittest.mock import patch
import project_recovery_service as s

class RecoveryCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.executescript("""CREATE TABLE ai_file_effect_outbox(
        id TEXT,project_id TEXT,artifact_id TEXT,receipt_json TEXT,
        pending_name TEXT,kind TEXT,state TEXT,created_at TEXT);
        CREATE TABLE manager_documents(id TEXT,project_id TEXT,kind TEXT,title TEXT);
        CREATE TABLE ai_pending_tasks(project_id TEXT, request TEXT);
        CREATE TABLE ai_completed_artifacts(project_id TEXT,artifact_id TEXT);""")
        self.db.commit()
    def tearDown(self):self.db.close()
    def seed(self,pid='a'*32, request='original', pending='pending_text_task_'+ 'b'*32+'.json'):
        self.db.execute('INSERT INTO manager_documents VALUES(?,?,?,?)',('c'*32,pid,'strategy','Demo'))
        self.db.execute('INSERT INTO ai_pending_tasks VALUES(?,?)',(pid,request))
        self.db.execute('INSERT INTO ai_file_effect_outbox VALUES(?,?,?,?,?,?,?,?)',
          ('d'*32,pid,'c'*32,json.dumps({'request':'original'}),pending,'clear_pending','done','2026'))
        self.db.commit()
    def run_recovery(self):
        with patch.object(s,'drain_documents',return_value={'completed':1}), \
             patch.object(s,'drain_text',return_value={'completed':0}), \
             patch.object(s,'finalize_document',return_value=True) as finish:
            report=s.recover(self.db,lambda pid:'/tmp',lambda *args:None,lambda kind,title:'saved')
        return report,finish
    def test_recovery_closes_original_assignment(self):
        self.seed()
        result,finish=self.run_recovery()
        self.assertEqual(len(result.completed),1)
        self.assertEqual(result.completed[0].task_id,'b'*32)
        self.assertEqual(finish.call_args.kwargs['project_id'],'a'*32)
    def test_different_active_request_remains_untouched(self):
        self.seed(request='replacement')
        result,finish=self.run_recovery()
        self.assertEqual(result.completed,())
        finish.assert_not_called()
    def test_malformed_receipt_rejected(self):
        self.seed()
        self.db.execute('UPDATE ai_file_effect_outbox SET receipt_json=?',('{invalid',))
        self.db.commit()
        result,finish=self.run_recovery()
        self.assertEqual(len(result.rejected),1)
        finish.assert_not_called()
    def test_already_complete_is_not_replayed(self):
        self.seed()
        self.db.execute('INSERT INTO ai_completed_artifacts VALUES(?,?)',('a'*32,'c'*32))
        self.db.commit()
        result,finish=self.run_recovery()
        self.assertEqual(result.completed,())
        finish.assert_not_called()
    def test_foreign_project_cannot_be_finished(self):
        self.seed()
        self.db.execute('UPDATE ai_pending_tasks SET project_id=?',('e'*32,))
        self.db.commit()
        result,finish=self.run_recovery()
        self.assertEqual(result.completed,())
        finish.assert_not_called()

if __name__=='__main__':unittest.main()
