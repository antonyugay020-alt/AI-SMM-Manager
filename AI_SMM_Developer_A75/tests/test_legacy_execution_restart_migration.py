"""Legacy restart migration: real SQLite files, worker fencing and rollback.

A pre-constraint database may contain duplicate running attempts for one
project. Bootstrap must preserve tasks/checkpoints and install both UNIQUE
indexes only after all stale worker tokens have been fenced.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from application_bootstrap import open_application_database
from execution_coordinator import Attempt, ExecutionCoordinator
from task_lifecycle import TaskLifecycle


class LegacyExecutionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'legacy.sqlite'

    def _legacy_database(self, *, duplicate_beta=False):
        db = open_application_database(self.path, self.root)
        db.executemany(
            "INSERT INTO projects(id,name,created_at) VALUES(?,?,?)",
            [('alpha', 'Alpha', 'now'), ('beta', 'Beta', 'now')])
        tasks = TaskLifecycle(db)
        paused = tasks.activate('alpha', 'draft', 'question one')
        active = tasks.activate('alpha', 'publish', 'question two', force_new=True)
        other = tasks.activate('beta', 'independent', 'question three')
        db.execute("INSERT INTO ai_task_contexts VALUES(?,?,?)",
                   (paused, 'alpha', '{"checkpoint":"retained"}'))
        # Simulate a database created before project/task worker constraints.
        db.execute("DROP INDEX execution_one_running_task")
        db.execute("DROP INDEX execution_one_running_project")
        attempts = [
            Attempt('legacy-a-paused', 'alpha', paused),
            Attempt('legacy-a-active', 'alpha', active),
            Attempt('legacy-b-active', 'beta', other),
        ]
        if duplicate_beta:
            attempts.append(Attempt('legacy-b-duplicate', 'beta', other))
        db.executemany(
            """INSERT INTO execution_attempts
               (attempt_id,project_id,task_id,state,started_at)
               VALUES(?,?,?,'running','before-restart')""",
            [(a.attempt_id, a.project_id, a.task_id) for a in attempts])
        db.commit()
        db.close()
        return attempts, (paused, active, other)

    @staticmethod
    def _indexes(db):
        return {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'execution_one_running_%'")}

    def test_bootstrap_fences_legacy_duplicates_and_preserves_tasks(self):
        attempts, (paused, active, other) = self._legacy_database()
        db = open_application_database(self.path, self.root)
        self.addCleanup(db.close)
        self.assertEqual(db.execute(
            "SELECT count(*) FROM execution_attempts WHERE state='interrupted'").fetchone()[0], 3)
        self.assertEqual(self._indexes(db),
                         {'execution_one_running_task', 'execution_one_running_project'})
        self.assertEqual(db.execute(
            "SELECT status FROM ai_task_registry WHERE task_id=?", (paused,)).fetchone(), ('paused',))
        self.assertEqual(db.execute(
            "SELECT status FROM ai_task_registry WHERE task_id=?", (active,)).fetchone(), ('active',))
        self.assertEqual(db.execute(
            "SELECT status FROM ai_task_registry WHERE task_id=?", (other,)).fetchone(), ('active',))
        self.assertEqual(db.execute(
            "SELECT context_json FROM ai_task_contexts WHERE task_id=?", (paused,)).fetchone(),
                         ('{"checkpoint":"retained"}',))
        self.assertEqual(db.execute("SELECT count(*) FROM ai_pending_tasks").fetchone()[0], 2)
        engine = ExecutionCoordinator(db)
        for attempt in attempts:
            with self.assertRaises(ValueError):
                engine.verify(attempt)
        fresh = engine.begin('alpha', active)
        engine.verify(fresh)
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("""INSERT INTO execution_attempts
                (attempt_id,project_id,task_id,state,started_at)
                VALUES('rogue','alpha',?,'running','now')""", (paused,))
        self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_scoped_recovery_keeps_unrelated_running_worker(self):
        attempts, _ = self._legacy_database()
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        engine = ExecutionCoordinator(db)
        self.assertEqual(engine.recover(project_id='alpha'), 2)
        self.assertEqual(self._indexes(db),
                         {'execution_one_running_task', 'execution_one_running_project'})
        engine.verify(attempts[2])
        for attempt in attempts[:2]:
            with self.assertRaises(ValueError):
                engine.verify(attempt)

    def test_scoped_migration_fails_closed_if_other_project_has_duplicates(self):
        attempts, _ = self._legacy_database(duplicate_beta=True)
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        engine = ExecutionCoordinator(db)
        with self.assertRaises(sqlite3.IntegrityError):
            engine.recover(project_id='alpha')
        self.assertEqual(db.execute(
            "SELECT count(*) FROM execution_attempts WHERE state='running'").fetchone()[0], 4)
        self.assertEqual(self._indexes(db), set())
        self.assertEqual(engine.recover(), 4)
        self.assertEqual(self._indexes(db),
                         {'execution_one_running_task', 'execution_one_running_project'})
        for attempt in attempts:
            with self.assertRaises(ValueError):
                engine.verify(attempt)

    def test_bootstrap_rollback_if_worker_fence_fails(self):
        attempts, (paused, active, other) = self._legacy_database()
        db = sqlite3.connect(self.path)
        db.execute("""CREATE TRIGGER block_restart_fence BEFORE UPDATE OF state
            ON execution_attempts WHEN OLD.state='running'
            BEGIN SELECT RAISE(ABORT,'injected recovery failure'); END""")
        db.commit()
        db.close()
        with self.assertRaises(sqlite3.IntegrityError):
            open_application_database(self.path, self.root)
        raw = sqlite3.connect(self.path)
        self.addCleanup(raw.close)
        self.assertEqual(raw.execute(
            "SELECT count(*) FROM execution_attempts WHERE state='running'").fetchone()[0], 3)
        self.assertEqual(self._indexes(raw), set())
        self.assertEqual(raw.execute(
            "SELECT count(*) FROM ai_task_registry WHERE task_id IN (?,?,?)",
            (paused, active, other)).fetchone()[0], 3)
        self.assertEqual(raw.execute(
            "SELECT context_json FROM ai_task_contexts WHERE task_id=?", (paused,)).fetchone(),
                         ('{"checkpoint":"retained"}',))
        self.assertEqual(raw.execute("SELECT count(*) FROM ai_pending_tasks").fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
