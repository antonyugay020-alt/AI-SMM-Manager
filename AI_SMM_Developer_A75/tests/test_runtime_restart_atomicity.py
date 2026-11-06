"""Cross-service restart safety: the whole app recovers, not one adapter."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime
from sqlite_transaction_core import own


class RuntimeRestartAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.database=self.root/'state.sqlite'
        self.db=open_application_database(self.database,self.root)
        self.addCleanup(self._close)
        for pid in ('a','b'):
            self.db.execute('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)',(pid,pid,'now'))
            for part in ('originals','processed'):(self.root/pid/part).mkdir(parents=True)
        self.db.commit()
        self.core=ApplicationRuntime(self.db,lambda pid:self.root/pid)

    def _close(self):
        try:self.db.close()
        except Exception:pass

    def _input(self,pid,name):
        path=self.root/pid/'originals'/name
        Image.new('RGB',(2,2)).save(path)
        return str(path)

    def _running(self,pid):
        request='make '+pid
        task=self.core.executions.activate(pid,request,'go')
        receipt=self.core.executions.begin(pid,task)
        self.db.commit()
        source=self._input(pid,'source.png')
        batch=self.core.batches.create(pid,[source],'edit',0)
        claim=self.core.batches.advance(pid,batch.batch_id)
        return task,receipt,batch,claim

    def test_all_projects_fenced_without_losing_tasks_or_inputs(self):
        a=self._running('a');b=self._running('b')
        state=self.core.recover_inflight()
        self.assertEqual(state,{'execution_attempts':2,'batch_items':2})
        for pid,(_,receipt,batch,claim) in zip(('a','b'),(a,b)):
            with self.assertRaises(ValueError):self.core.executions.authorize_result(receipt,active_project_id=pid)
            with self.assertRaises(ValueError):
                with own(self.db): self.core.batches.authorize_in_transaction(claim)
            self.assertEqual(self.core.batches.pending(pid).paths,(claim.source_path,))
            new=self.core.batches.advance(pid,batch.batch_id)
            self.assertNotEqual(new.token,claim.token)
        self.assertEqual(self.core.recover_inflight()['execution_attempts'],0)

    def test_single_project_recovery_does_not_revoke_another(self):
        a=self._running('a');b=self._running('b')
        self.assertEqual(self.core.recover_inflight(project_id='a'),{'execution_attempts':1,'batch_items':1})
        self.core.executions.authorize_result(b[1],active_project_id='b')
        with own(self.db):
            self.core.batches.authorize_in_transaction(b[3])
        with self.assertRaises(ValueError):self.core.executions.authorize_result(a[1],active_project_id='a')

    def test_sqlite_failure_rolls_back_both_worker_kinds(self):
        a=self._running('a')
        self.db.execute('''CREATE TRIGGER reject_restart BEFORE UPDATE OF status ON batch_item_receipts
            WHEN NEW.status='failed' BEGIN SELECT RAISE(ABORT,'restart denied'); END''')
        self.db.commit()
        with self.assertRaises(sqlite3.DatabaseError):self.core.recover_inflight()
        self.assertFalse(self.db.in_transaction)
        self.core.executions.authorize_result(a[1],active_project_id='a')
        with own(self.db):self.core.batches.authorize_in_transaction(a[3])
        self.db.execute('DROP TRIGGER reject_restart');self.db.commit()
        self.assertEqual(self.core.recover_inflight(),{'execution_attempts':1,'batch_items':1})

    def test_external_sqlite_transaction_not_committed(self):
        self._running('a')
        self.db.execute("UPDATE projects SET name='other' WHERE id='a'")
        with self.assertRaises(RuntimeError):self.core.recover_inflight()
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT name FROM projects WHERE id='a'").fetchone(),('a',))

    def test_persisted_restart_replays_only_inflight(self):
        a=self._running('a')
        self.db.close()
        self.db=open_application_database(self.database,self.root)
        self.core=ApplicationRuntime(self.db,lambda pid:self.root/pid)
        # Opening the database fences stale process attempts as part of the
        # schema migration, before any new Qt/runtime worker can own them.
        # Explicit recovery must therefore be idempotent for execution attempts.
        self.assertEqual(self.core.recover_inflight(),{'execution_attempts':0,'batch_items':1})
        with self.assertRaises(ValueError):
            self.core.executions.authorize_result(a[1],active_project_id='a')
        self.assertEqual(self.db.execute('SELECT state FROM execution_attempts WHERE attempt_id=?',(a[1].attempt_id,)).fetchone(),('interrupted',))
        self.assertEqual(self.core.batches.pending('a').paths,(a[3].source_path,))
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(a[0],)).fetchone(),('active',))

if __name__=='__main__':unittest.main()
