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

    def test_current_schema_defers_worker_fencing_to_application_recovery(self):
        db = open_application_database(self.path, self.root)
        db.execute("INSERT INTO projects(id,name,created_at) VALUES('alpha','Alpha','now')")
        task = TaskLifecycle(db).activate('alpha', 'current', 'question')
        old = ExecutionCoordinator(db).begin('alpha', task)
        db.commit()
        db.close()
        reopened = open_application_database(self.path, self.root)
        self.addCleanup(reopened.close)
        engine = ExecutionCoordinator(reopened)
        # Existing schema must not steal the app-level cross-service restart
        # boundary: execution and batch workers are fenced together later.
        engine.verify(old)
        self.assertEqual(engine.recover(), 1)
        with self.assertRaises(ValueError):
            engine.verify(old)

    def test_legacy_batch_and_execution_migrate_in_one_transaction(self):
        attempts, _ = self._legacy_database()
        raw = sqlite3.connect(self.path)
        raw.execute("""INSERT INTO batch_runs
            (id,project_id,prompt,engine_mode,queue_json,total,status,updated_at)
            VALUES('legacy-batch','alpha','recover',0,'["source.png"]',1,'running','old')""")
        raw.execute("""INSERT INTO batch_item_receipts
            (batch_id,position,project_id,source_path,status,attempt_token,updated_at)
            VALUES('legacy-batch',0,'alpha','source.png','running','stale-token','old')""")
        raw.execute("""CREATE TRIGGER reject_legacy_batch_fence
            BEFORE UPDATE OF status ON batch_item_receipts
            WHEN NEW.status='failed'
            BEGIN SELECT RAISE(ABORT,'injected batch migration failure'); END""")
        raw.commit()
        raw.close()
        # A batch failure must roll back execution fencing AND index creation.
        with self.assertRaises(sqlite3.IntegrityError):
            open_application_database(self.path, self.root)
        raw = sqlite3.connect(self.path)
        self.assertEqual(raw.execute(
            "SELECT COUNT(*) FROM execution_attempts WHERE state='running'").fetchone()[0], 3)
        self.assertEqual(raw.execute(
            "SELECT status,attempt_token FROM batch_item_receipts").fetchone(),
            ('running','stale-token'))
        self.assertEqual(self._indexes(raw), set())
        raw.execute('DROP TRIGGER reject_legacy_batch_fence')
        raw.commit()
        raw.close()
        migrated = open_application_database(self.path, self.root)
        self.addCleanup(migrated.close)
        self.assertEqual(migrated.execute(
            "SELECT COUNT(*) FROM execution_attempts WHERE state='interrupted'").fetchone()[0], 3)
        self.assertEqual(migrated.execute(
            "SELECT status,attempt_token FROM batch_item_receipts").fetchone(),
            ('failed',None))
        self.assertEqual(self._indexes(migrated),
                         {'execution_one_running_task', 'execution_one_running_project'})
        for attempt in attempts:
            with self.assertRaises(ValueError):
                ExecutionCoordinator(migrated).verify(attempt)

    def test_partially_missing_index_fences_both_worker_kinds(self):
        db = open_application_database(self.path, self.root)
        db.execute("INSERT INTO projects(id,name,created_at) VALUES('alpha','Alpha','now')")
        task = TaskLifecycle(db).activate('alpha','partial-index','question')
        old = ExecutionCoordinator(db).begin('alpha',task)
        db.execute("""INSERT INTO batch_runs
            (id,project_id,prompt,engine_mode,queue_json,total,status,updated_at)
            VALUES('partial-batch','alpha','recover',0,'["source.png"]',1,'running','old')""")
        db.execute("""INSERT INTO batch_item_receipts
            (batch_id,position,project_id,source_path,status,attempt_token,updated_at)
            VALUES('partial-batch',0,'alpha','source.png','running','stale-token','old')""")
        db.execute('DROP INDEX execution_one_running_project')
        db.commit()
        db.close()
        migrated = open_application_database(self.path,self.root)
        self.addCleanup(migrated.close)
        self.assertEqual(migrated.execute(
            'SELECT state FROM execution_attempts WHERE attempt_id=?',
            (old.attempt_id,)).fetchone(),('interrupted',))
        self.assertEqual(migrated.execute(
            'SELECT status,attempt_token FROM batch_item_receipts').fetchone(),
            ('failed',None))
        self.assertEqual(self._indexes(migrated),
                         {'execution_one_running_task','execution_one_running_project'})

    def test_bootstrap_repairs_nonunique_named_indexes_and_fences_duplicates(self):
        attempts, (paused, active, _) = self._legacy_database()
        raw = sqlite3.connect(self.path)
        raw.execute('CREATE INDEX execution_one_running_task ON execution_attempts(task_id)')
        raw.execute('CREATE INDEX execution_one_running_project ON execution_attempts(project_id)')
        raw.commit()
        raw.close()
        migrated = open_application_database(self.path, self.root)
        self.addCleanup(migrated.close)
        self.assertEqual(migrated.execute(
            "SELECT count(*) FROM execution_attempts WHERE state='interrupted'").fetchone()[0], 3)
        indexes = {r[1]: (r[2], r[4]) for r in migrated.execute(
            "PRAGMA index_list('execution_attempts')")}
        for name in self._indexes(migrated):
            self.assertEqual(indexes[name], (1, 1))
        for attempt in attempts:
            with self.assertRaises(ValueError):
                ExecutionCoordinator(migrated).verify(attempt)
        migrated.execute("""INSERT INTO execution_attempts
            (attempt_id,project_id,task_id,state,started_at)
            VALUES('new-one','alpha',?,'running','now')""", (paused,))
        with self.assertRaises(sqlite3.IntegrityError):
            migrated.execute("""INSERT INTO execution_attempts
                (attempt_id,project_id,task_id,state,started_at)
                VALUES('new-two','alpha',?,'running','now')""", (active,))

    def test_bootstrap_repairs_wrong_partial_predicate_with_expected_names(self):
        attempts, _ = self._legacy_database()
        raw = sqlite3.connect(self.path)
        for name, column in (('execution_one_running_task', 'task_id'),
                             ('execution_one_running_project', 'project_id')):
            raw.execute(f"""CREATE UNIQUE INDEX {name}
                ON execution_attempts({column}) WHERE state='accepted'""")
        raw.commit()
        raw.close()
        migrated = open_application_database(self.path, self.root)
        self.addCleanup(migrated.close)
        self.assertEqual(migrated.execute(
            "SELECT count(*) FROM execution_attempts WHERE state='running'").fetchone()[0], 0)
        self.assertEqual(ExecutionCoordinator(migrated)._invalid_constraints(), [])
        for attempt in attempts:
            with self.assertRaises(ValueError):
                ExecutionCoordinator(migrated).verify(attempt)

    def test_live_worker_operations_refuse_noncanonical_index_authority(self):
        db = open_application_database(self.path, self.root)
        db.execute("INSERT INTO projects(id,name,created_at) VALUES('alpha','Alpha','now')")
        task = TaskLifecycle(db).activate('alpha', 'index-contract', 'question')
        for name, column in (('execution_one_running_task', 'task_id'),
                             ('execution_one_running_project', 'project_id')):
            db.execute(f'DROP INDEX {name}')
            db.execute(f'CREATE INDEX {name} ON execution_attempts({column})')
        db.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            ExecutionCoordinator(db).begin('alpha', task)
        self.assertEqual(db.execute('SELECT count(*) FROM execution_attempts').fetchone()[0], 0)
        db.close()
        migrated = open_application_database(self.path, self.root)
        self.addCleanup(migrated.close)
        self.assertEqual(ExecutionCoordinator(migrated)._invalid_constraints(), [])
        fresh = ExecutionCoordinator(migrated).begin('alpha', task)
        ExecutionCoordinator(migrated).verify(fresh)

    def test_noncanonical_index_and_batch_fence_failure_roll_back_together(self):
        attempts, _ = self._legacy_database()
        raw = sqlite3.connect(self.path)
        raw.execute('CREATE INDEX execution_one_running_task ON execution_attempts(task_id)')
        raw.execute('CREATE INDEX execution_one_running_project ON execution_attempts(project_id)')
        raw.execute("""INSERT INTO batch_runs
            (id,project_id,prompt,engine_mode,queue_json,total,status,updated_at)
            VALUES('legacy-batch','alpha','recover',0,'["source.png"]',1,'running','old')""")
        raw.execute("""INSERT INTO batch_item_receipts
            (batch_id,position,project_id,source_path,status,attempt_token,updated_at)
            VALUES('legacy-batch',0,'alpha','source.png','running','old-token','old')""")
        raw.execute("""CREATE TRIGGER reject_batch_fence BEFORE UPDATE OF status
            ON batch_item_receipts WHEN NEW.status='failed'
            BEGIN SELECT RAISE(ABORT,'injected batch failure'); END""")
        raw.commit()
        raw.close()
        with self.assertRaises(sqlite3.IntegrityError):
            open_application_database(self.path, self.root)
        raw = sqlite3.connect(self.path)
        self.assertEqual(raw.execute(
            "SELECT count(*) FROM execution_attempts WHERE state='running'").fetchone()[0], 3)
        self.assertEqual(raw.execute(
            'SELECT status,attempt_token FROM batch_item_receipts').fetchone(),
            ('running', 'old-token'))
        self.assertEqual({r[1]: r[2] for r in raw.execute(
            "PRAGMA index_list('execution_attempts')") if r[1].startswith('execution_one_running_')},
            {'execution_one_running_task': 0, 'execution_one_running_project': 0})
        raw.execute('DROP TRIGGER reject_batch_fence')
        raw.commit()
        raw.close()
        migrated = open_application_database(self.path, self.root)
        self.addCleanup(migrated.close)
        self.assertEqual(ExecutionCoordinator(migrated)._invalid_constraints(), [])
        self.assertEqual(migrated.execute(
            'SELECT status,attempt_token FROM batch_item_receipts').fetchone(), ('failed', None))
        for attempt in attempts:
            with self.assertRaises(ValueError):
                ExecutionCoordinator(migrated).verify(attempt)

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
