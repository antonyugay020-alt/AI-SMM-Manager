"""Transactional upgrade contract for durable media receipts."""
import sqlite3
import unittest

from media_ingest_core import install


OLD_SCHEMA = """
CREATE TABLE media_ingest_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('asset','reference')),
    source_sha256 TEXT NOT NULL, source_size INTEGER NOT NULL,
    filename TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','done')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""


class RenameFaultConnection(sqlite3.Connection):
    fail_rename = False

    def execute(self, sql, parameters=()):
        if self.fail_rename and "ALTER TABLE media_ingest_jobs_v2 RENAME" in sql:
            self.fail_rename = False
            raise sqlite3.OperationalError("simulated crash before rename")
        return super().execute(sql, parameters)


class MediaMigrationAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", factory=RenameFaultConnection)
        self.addCleanup(self.db.close)
        self.db.execute(OLD_SCHEMA)
        self.db.execute(
            """INSERT INTO media_ingest_jobs
            (id,project_id,kind,source_sha256,source_size,filename,state,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            ("a"*32, "b"*32, "asset", "c"*64, 3, "a"*32+".png", "pending", "old"))
        self.db.commit()

    def test_failed_upgrade_preserves_original_schema_and_receipt(self):
        self.db.fail_rename = True
        with self.assertRaisesRegex(sqlite3.OperationalError, "simulated crash"):
            install(self.db)
        self.assertFalse(self.db.in_transaction)
        sql = self.db.execute(
            "SELECT sql FROM sqlite_master WHERE name='media_ingest_jobs'").fetchone()[0]
        self.assertNotIn("'result'", sql)
        self.assertEqual(self.db.execute(
            "SELECT id,state FROM media_ingest_jobs").fetchall(), [("a"*32, "pending")])
        self.assertIsNone(self.db.execute(
            "SELECT name FROM sqlite_master WHERE name='media_ingest_jobs_v2'").fetchone())
        install(self.db)
        sql = self.db.execute(
            "SELECT sql FROM sqlite_master WHERE name='media_ingest_jobs'").fetchone()[0]
        self.assertIn("'result'", sql)
        self.assertEqual(self.db.execute(
            "SELECT id,state FROM media_ingest_jobs").fetchall(), [("a"*32, "pending")])

    def test_upgrade_respects_outer_transaction_rollback(self):
        self.db.execute("BEGIN IMMEDIATE")
        install(self.db)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        sql = self.db.execute(
            "SELECT sql FROM sqlite_master WHERE name='media_ingest_jobs'").fetchone()[0]
        self.assertNotIn("'result'", sql)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM media_ingest_jobs").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
