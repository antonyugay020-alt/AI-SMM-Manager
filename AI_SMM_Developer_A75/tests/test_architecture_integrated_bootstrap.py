import sqlite3
import tempfile
import unittest
from pathlib import Path
from application_bootstrap import install_schema, open_application_database
from application_core import ApplicationRuntime

class IntegratedBootstrap(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=open_application_database(self.root/'manager.sqlite',self.root)
        self.addCleanup(self.db.close)
        self.db.executemany('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)', [('p1','one','now'),('p2','two','now')])
        self.db.commit()
        self.core=ApplicationRuntime(self.db,lambda x:self.root/x)

    def test_cross_project_task_receipts_survive_reopen(self):
        a=self.core.outcomes.park_and_reply('p1','request A','question','answer A')
        b=self.core.outcomes.park_and_reply('p2','request B','question','answer B')
        self.assertNotEqual(a.task_id,b.task_id)
        self.assertEqual(self.db.execute('select count(*) from ai_task_registry').fetchone()[0],2)
        reopened=sqlite3.connect(self.root/'manager.sqlite')
        self.assertEqual(reopened.execute("select count(*) from messages where role='assistant'").fetchone()[0],2)
        reopened.close()

    def test_invalid_revision_rollback(self):
        with self.assertRaises(ValueError):
            self.core.outcomes.park_and_reply('p1','request','q','response',revision_id='invalid')
        self.assertEqual(self.db.execute('select count(*) from messages').fetchone()[0],0)
        self.assertEqual(self.db.execute('select count(*) from ai_task_registry').fetchone()[0],0)

    def test_assistant_insert_error_rolls_back_task(self):
        self.db.execute("""CREATE TRIGGER reject_assistant BEFORE INSERT ON messages
            WHEN NEW.role='assistant' BEGIN SELECT RAISE(FAIL,'cannot insert assistant'); END""")
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.core.outcomes.park_and_reply('p1','request','q','response')
        self.assertEqual(self.db.execute('select count(*) from ai_task_registry').fetchone()[0],0)
        self.assertEqual(self.db.execute('select count(*) from ai_pending_tasks').fetchone()[0],0)

    def test_foreign_transaction_not_committed(self):
        self.db.execute("UPDATE projects SET name='changed' WHERE id='p1'")
        with self.assertRaises(RuntimeError):
            self.core.outcomes.park_and_reply('p2','request','q','response')
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT name FROM projects WHERE id='p1'").fetchone()[0],'one')

    def test_schema_idempotent_and_runtime_tables_exist(self):
        install_schema(self.db)
        for table in ('ai_task_registry','execution_attempts','ai_dialogue_task_links',
                      'manager_document_exports','result_publication_events',
                      'ai_task_transitions','ai_dialogue_publications',
                      'project_text_projections','media_ingest_jobs','ai_file_effect_outbox'):
            self.assertIsNotNone(self.db.execute('SELECT name FROM sqlite_master WHERE name=?',(table,)).fetchone(),table)
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_schema_refuses_foreign_transaction(self):
        self.db.execute("UPDATE projects SET name='not committed' WHERE id='p1'")
        with self.assertRaises(RuntimeError):install_schema(self.db)
        self.db.rollback()

    def test_legacy_schema_migration(self):
        original=sqlite3.connect(self.root/'legacy.sqlite')
        original.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_at TEXT NOT NULL)')
        original.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT NOT NULL,question TEXT NOT NULL)')
        original.commit()
        original.close()
        reopened=open_application_database(self.root/'legacy.sqlite',self.root)
        self.assertIn('revision_id',{r[1] for r in reopened.execute('PRAGMA table_info(ai_pending_tasks)')})
        self.assertEqual(reopened.execute('pragma integrity_check').fetchone()[0],'ok')
        reopened.close()
