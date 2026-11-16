"""Whole-project deletion is a durable transaction, not a reset of metadata."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from application_bootstrap import install_schema
from project_workspace_runtime import ProjectWorkspaceResolver
from project_control_runtime import ProjectControlRuntime
from project_deletion import recover_deletions

class ProjectRegistryDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        install_schema(self.db)
        self.a,self.b='a'*32,'b'*32
        for pid in (self.a,self.b):
            folder=self.root/'disconnected-volume'/pid
            folder.mkdir(parents=True)
            (folder/'note.txt').write_text(pid, encoding='utf-8')
            self.db.execute('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)',(pid,pid,'now'))
            self.db.execute('INSERT INTO project_locations VALUES(?,?)',(pid,str(folder)))
            self.db.execute('INSERT INTO messages(project_id,role,content,created_at) VALUES(?,?,?,?)',
                            (pid,'user','unfinished '+pid,'now'))
            self.db.execute('INSERT INTO ai_pending_tasks(project_id,request,question) VALUES(?,?,?)',
                            (pid,'instruction '+pid,'pending'))
        self.db.commit()
        self.resolver=ProjectWorkspaceResolver(self.db)
        self.control=ProjectControlRuntime(self.db,self.resolver,self.root/'backups')

    def test_delete_one_project_removes_its_identity_without_touching_unfinished_neighbor(self):
        location=self.resolver(self.a)
        receipt=self.control.backup_and_delete(self.a,'project')
        self.assertTrue(receipt.backup.is_file())
        self.assertFalse(location.exists())
        self.assertIsNone(self.db.execute('SELECT id FROM projects WHERE id=?',(self.a,)).fetchone())
        self.assertIsNone(self.db.execute('SELECT folder FROM project_locations WHERE project_id=?',(self.a,)).fetchone())
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks WHERE project_id=?',(self.b,)).fetchone(),
                         ('instruction '+self.b,))
        self.assertEqual(self.db.execute('SELECT content FROM messages WHERE project_id=?',(self.b,)).fetchone(),
                         ('unfinished '+self.b,))
        self.assertEqual(list(self.db.execute('PRAGMA foreign_key_check')),[])
        with self.assertRaises(ValueError):self.resolver(self.a)

    def test_committed_cleanup_recovers_without_deleted_location_in_database(self):
        folder=self.resolver(self.a)
        import shutil
        original=shutil.rmtree
        def interrupted(path,*args,**kwargs):
            if '.ai_smm_delete_staging' in str(path):raise OSError('crash after SQL commit')
            return original(path,*args,**kwargs)
        with patch('project_deletion.shutil.rmtree',side_effect=interrupted):
            self.control.backup_and_delete(self.a,'project')
        self.assertIsNone(self.db.execute('SELECT id FROM projects WHERE id=?',(self.a,)).fetchone())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_delete_commit_marks').fetchone()[0],1)
        # Only B is registered; the tombstone must locate A's missing volume.
        recover_deletions(self.db,[str(self.resolver(self.b))],self.root/'unrelated-default')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_delete_commit_marks').fetchone()[0],0)
        self.assertFalse(folder.exists())

    def test_running_execution_claim_blocks_delete_without_writing_backup(self):
        self.db.execute('''INSERT INTO ai_task_registry
            (task_id,project_id,request,question,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)''',('task',self.a,'request','question','active','now','now'))
        self.db.execute('''INSERT INTO execution_attempts
            (attempt_id,project_id,task_id,state,started_at) VALUES(?,?,?,?,?)''',
            ('attempt',self.a,'task','running','now'))
        self.db.commit()
        with self.assertRaises(RuntimeError):self.control.backup_and_delete(self.a,'project')
        self.assertTrue(self.resolver(self.a).exists())
        self.assertFalse((self.root/'backups').exists())
        self.db.execute("UPDATE execution_attempts SET state='interrupted' WHERE attempt_id='attempt'")
        self.db.commit()
        self.control.backup_and_delete(self.a,'project')
        self.assertIsNone(self.db.execute('SELECT id FROM projects WHERE id=?',(self.a,)).fetchone())

    def test_running_batch_claim_blocks_delete_without_writing_backup(self):
        self.db.execute('''INSERT INTO batch_runs
            (id,project_id,prompt,engine_mode,queue_json,total,status,updated_at)
            VALUES(?,?,?,?,?,?,?,?)''',('batch',self.a,'task',0,'[]',1,'running','now'))
        self.db.execute('''INSERT INTO batch_item_receipts
            (batch_id,position,project_id,source_path,status,updated_at)
            VALUES(?,?,?,?,?,?)''',('batch',0,self.a,'source','running','now'))
        self.db.commit()
        with self.assertRaises(RuntimeError):self.control.backup_and_delete(self.a,'project')
        self.assertFalse((self.root/'backups').exists())
        self.assertTrue(self.resolver(self.a).exists())

    def test_unrelated_transaction_cannot_be_committed_by_deletion(self):
        self.db.execute('UPDATE projects SET name=? WHERE id=?',('dirty',self.b))
        with self.assertRaises(RuntimeError):self.control.backup_and_delete(self.a,'project')
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertTrue(self.resolver(self.a).exists())

if __name__=='__main__':unittest.main()
