"""Regression: delayed completion cannot close a different same-worded task."""
from test_r107_multi_task_isolation import MultiTaskTests
from task_registry import finish
import sqlite3


class FinalizationIdentityTests(MultiTaskTests):
    def test_stale_identical_request_result_never_closes_successor(self):
        old = self.create('a', 'Prepare a complete calendar')
        current = self.create('a', 'Prepare a complete calendar')
        self.assertNotEqual(old, current)
        with self.assertRaisesRegex(ValueError, 'идентификатору'):
            finish(self.db, 'a', 'Prepare a complete calendar', task_id=old)
        with self.assertRaisesRegex(ValueError, 'неоднозначному'):
            finish(self.db, 'a', 'Prepare a complete calendar')
        self.assertEqual(self.db.execute('SELECT task_id FROM ai_task_registry WHERE status="active"').fetchone()[0], current)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?', (old,)).fetchone()[0], 'paused')
        finish(self.db, 'a', 'Prepare a complete calendar', task_id=current)
        self.assertIsNone(self.db.execute('SELECT * FROM ai_pending_tasks').fetchone())
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?', (old,)).fetchone()[0], 'paused')

    def test_same_request_in_neighbor_project_must_not_affect_ownership(self):
        a = self.create('a', 'Identical prompt')
        b = self.create('b', 'Identical prompt')
        with self.assertRaises(ValueError):
            finish(self.db, 'a', 'Identical prompt', task_id=b)
        finish(self.db, 'b', 'Identical prompt', task_id=b)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(a,)).fetchone()[0], 'active')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(b,)).fetchone()[0], 'completed')

    def test_legacy_completion_without_id_only_when_request_unambiguous(self):
        ident = self.create('a', 'Unique task')
        self.assertEqual(finish(self.db, 'a', 'Unique task'), ident)

    def test_diagnostic_checkpoints_use_task_identity(self):
        from task_checkpoint import checkpoint_path
        old = self.create('a', 'Same brief')
        current = self.create('a', 'Same brief')
        first=checkpoint_path(self.tmp.name, 'task_lifecycle', 'Same brief', old)
        second=checkpoint_path(self.tmp.name, 'task_lifecycle', 'Same brief', current)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, second.parent)
        self.assertRaises(ValueError, checkpoint_path, self.tmp.name,
                          'task_lifecycle', 'Same brief', '../../bad')

    def test_trigger_failure_does_not_mark_task_completed(self):
        ident = self.create('a', 'Unique task')
        self.db.commit()
        self.db.execute('''CREATE TRIGGER fail_pending_delete BEFORE DELETE ON ai_pending_tasks
            BEGIN SELECT RAISE(ABORT,'refuse deletion'); END''')
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            finish(self.db, 'a', 'Unique task', task_id=ident)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?', (ident,)).fetchone()[0],'active')
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM ai_pending_tasks WHERE project_id="a"').fetchone())
