"""Real SQLite reopen tests, not just in-memory transition mocks."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from task_lifecycle import TaskLifecycle
from execution_coordinator import ExecutionCoordinator

class DurableAttemptTests(unittest.TestCase):
    def test_process_restart_retains_project_task_and_fences_old_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'tasks.sqlite'
            db=sqlite3.connect(path)
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT NOT NULL,question TEXT NOT NULL,revision_id TEXT)')
            db.executemany('INSERT INTO projects VALUES(?)',[('a',),('b',)])
            db.commit()
            lifecycle=TaskLifecycle(db)
            ta=lifecycle.activate('a','publish 12','work')
            tb=lifecycle.activate('b','draft 16','work')
            engine=ExecutionCoordinator(db)
            olda=engine.begin('a',ta)
            oldb=engine.begin('b',tb)
            db.commit()
            db.close()
            db=sqlite3.connect(path)
            db.execute('PRAGMA foreign_keys=ON')
            engine=ExecutionCoordinator(db)
            self.assertEqual(engine.recover(),2)
            db.commit()
            for attempt in (olda,oldb):
                with self.assertRaises(ValueError):engine.verify(attempt)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_task_registry WHERE status='active'").fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ai_pending_tasks').fetchone()[0],2)
            renewed=engine.begin('a',ta)
            engine.verify(renewed)
            self.assertEqual(engine.recover(project_id='a'),1)
            self.assertEqual(db.execute("SELECT state FROM execution_attempts WHERE attempt_id=?",(oldb.attempt_id,)).fetchone()[0],'interrupted')
            db.close()

if __name__=='__main__': unittest.main()
