"""Cross-project content boundaries: imports, commands, documents and crash replay."""
import sqlite3
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime
from media_ingest_core import replay


class ContentBoundarySystemTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.db=open_application_database(self.root/'data.sqlite',self.root)
        self.addCleanup(self.db.close)
        self.pids=('a'*32,'b'*32)
        for pid in self.pids:
            self.db.execute('INSERT INTO projects(id,name,created_at) VALUES (?,?,?)',(pid,pid,'now'))
            (self.root/pid/'processed').mkdir(parents=True)
        self.db.commit()
        self.core=ApplicationRuntime(self.db,lambda pid:self.root/pid)
        self.source=self.root/'sample.png';self.source.write_bytes(b'input-bytes')

    def test_manual_import_and_local_instruction_preserve_project_isolation(self):
        a,b=self.pids
        item=self.core.import_result_file(a,self.source)
        self.assertTrue(item.committed and item.published)
        self.assertEqual(item.final_path.read_bytes(),b'input-bytes')
        self.assertEqual(self.db.execute('SELECT kind FROM project_files WHERE id=?',(item.item_id,)).fetchone(),('result',))
        self.core.record_local_command(b,'удали документ',timestamp='now')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages WHERE project_id=?',(a,)).fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT role,content FROM messages WHERE project_id=?',(b,)).fetchone(),('user','удали документ'))

    def test_import_replays_after_commit_when_disk_publish_fails(self):
        a,b=self.pids
        original_link=os.link
        def interrupt_publish(source,target):
            if Path(target).parent.name=='processed':raise OSError('interrupted')
            return original_link(source,target)
        with patch('media_ingest_core.os.link',side_effect=interrupt_publish):
            imported=self.core.import_result_file(a,self.source)
        self.assertTrue(imported.committed)
        self.assertFalse(imported.published)
        self.assertEqual(self.db.execute('SELECT state FROM media_ingest_jobs WHERE id=?',(imported.item_id,)).fetchone(),('pending',))
        self.source.unlink()
        self.assertEqual(replay(self.db,lambda pid:self.root/pid,project_id=a)['completed'],1)
        self.assertEqual(imported.final_path.read_bytes(),b'input-bytes')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files WHERE project_id=?',(b,)).fetchone()[0],0)

    def test_import_fail_and_retry_respects_foreign_transaction(self):
        a,_=self.pids
        self.db.execute('UPDATE projects SET name=? WHERE id=?',('uncommitted',a))
        with self.assertRaises(RuntimeError):self.core.import_result_file(a,self.source)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT name FROM projects WHERE id=?',(a,)).fetchone(),(a,))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM media_ingest_jobs').fetchone()[0],0)

    def test_existing_media_schema_migration_preserves_old_receipts(self):
        self.db.close()
        db=sqlite3.connect(self.root/'legacy.sqlite')
        db.execute('''CREATE TABLE media_ingest_jobs (
            id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL
            CHECK(kind IN ('asset','reference')),source_sha256 TEXT NOT NULL,
            source_size INTEGER NOT NULL,filename TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('pending','done')),
            attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL)''')
        db.execute("INSERT INTO media_ingest_jobs VALUES ('old','a','reference','digest',1,'file.png','done',2,'','before')")
        db.commit();db.close()
        migrated=open_application_database(self.root/'legacy.sqlite',self.root)
        try:
            self.assertEqual(migrated.execute('SELECT id,kind,attempts FROM media_ingest_jobs').fetchone(),('old','reference',2))
            migrated.execute("INSERT INTO media_ingest_jobs VALUES ('new','a','result','digest',1,'file.png','pending',0,'','now')")
            migrated.commit()
            self.assertEqual(migrated.execute("SELECT COUNT(*) FROM media_ingest_jobs WHERE kind='result'").fetchone(),(1,))
        finally:migrated.close()

    def test_retry_does_not_replace_foreign_bytes(self):
        a,_=self.pids
        fixed='12345678123412341234123412341234'
        target=self.root/a/'processed'/(fixed+'.png')
        target.write_bytes(b'secret-data')
        with self.assertRaises(FileExistsError):self.core.import_result_file(a,self.source,item_id=fixed)
        self.assertEqual(target.read_bytes(),b'secret-data')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone(),(0,))


if __name__=='__main__':unittest.main()
