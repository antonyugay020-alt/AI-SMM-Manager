import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from media_ingest_core import ingest,replay,_digest

class DurableMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.db=sqlite3.connect(self.base/'main.sqlite')
        self.db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.p1='a'*32;self.p2='b'*32
        self.db.executemany('INSERT INTO projects VALUES(?)',[(self.p1,),(self.p2,)])
        self.db.commit()
        self.src=self.base/'source.png';self.src.write_bytes(b'valid-binary-source')

    def tearDown(self):self.db.close()

    def workspace(self,pid):return self.base/'projects'/pid

    def test_asset_publication_and_duplicate_are_project_scoped(self):
        first=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        self.assertTrue(first.committed and first.published)
        self.assertEqual(first.final_path.read_bytes(),self.src.read_bytes())
        dup=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        self.assertTrue(dup.duplicate)
        self.assertEqual(dup.final_path,first.final_path)
        second=ingest(self.db,self.workspace,self.src,project_id=self.p2,kind='asset')
        self.assertNotEqual(first.final_path,second.final_path)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM assets').fetchone()[0],2)

    def test_reference_registers_only_its_original_project(self):
        receipt=ingest(self.db,self.workspace,self.src,project_id=self.p2,kind='reference')
        self.assertTrue(receipt.published)
        row=self.db.execute('SELECT project_id,kind,path FROM project_files WHERE id=?',(receipt.item_id,)).fetchone()
        self.assertEqual((row[0],row[1]),(self.p2,'reference'))
        self.assertEqual(row[2],str(receipt.final_path))

    def test_simulated_crash_after_sql_commit_replays_without_source(self):
        import media_ingest_core
        real_replay=media_ingest_core.replay
        with patch.object(media_ingest_core,'replay',return_value={'completed':0,'failed':0,'pending':1}):
            receipt=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        self.assertTrue(receipt.committed)
        self.assertFalse(receipt.published)
        self.src.unlink()
        self.db.close()
        self.db=sqlite3.connect(self.base/'main.sqlite')
        result=real_replay(self.db,self.workspace)
        self.assertEqual(result['completed'],1)
        self.assertTrue(receipt.final_path.is_file())
        self.assertEqual(receipt.final_path.read_bytes(),b'valid-binary-source')

    def test_conflicting_destination_is_never_overwritten(self):
        import media_ingest_core
        with patch.object(media_ingest_core,'replay',return_value={'completed':0,'failed':0,'pending':1}):
            receipt=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        receipt.final_path.parent.mkdir(parents=True,exist_ok=True)
        receipt.final_path.write_bytes(b'foreign-data')
        result=replay(self.db,self.workspace)
        self.assertEqual(result['failed'],1)
        self.assertEqual(receipt.final_path.read_bytes(),b'foreign-data')
        self.assertEqual(self.db.execute('SELECT state FROM media_ingest_jobs').fetchone()[0],'pending')

    def test_missing_or_corrupt_spool_is_not_accepted(self):
        import media_ingest_core
        with patch.object(media_ingest_core,'replay',return_value={'completed':0,'failed':0,'pending':1}):
            receipt=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        spool=list((self.workspace(self.p1)/'.ingest_spool').iterdir())
        for path in spool:path.unlink()
        self.assertEqual(replay(self.db,self.workspace)['failed'],1)
        self.assertFalse(receipt.final_path.exists())

    def test_db_rejection_keeps_registered_assets_empty(self):
        self.db.execute("CREATE TRIGGER reject_asset BEFORE INSERT ON assets BEGIN SELECT RAISE(ABORT,'no'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        self.assertEqual(self.db.execute('SELECT count(*) FROM assets').fetchone()[0],0)
        self.assertFalse(self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_ingest_jobs'").fetchone())

    def test_foreign_transaction_not_committed(self):
        self.db.execute('CREATE TABLE unrelated(message TEXT)')
        self.db.execute('INSERT INTO unrelated VALUES(?)',('unsaved',))
        with self.assertRaises(RuntimeError):
            ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT message FROM unrelated').fetchone()[0],'unsaved')

if __name__=='__main__':unittest.main()

class LegacyMediaRecoveryTests(unittest.TestCase):
    def test_reimport_recovers_legacy_asset_with_separate_uuid_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);pid='c'*32;ident='d'*32;basename='e'*32+'.png'
            source=base/'source.png';source.write_bytes(b'original-bytes')
            db=sqlite3.connect(':memory:')
            try:
                db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
                db.execute('INSERT INTO projects VALUES(?)',(pid,))
                db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)')
                db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
                import hashlib
                target=base/pid/'originals'/basename
                db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?)',(ident,pid,source.name,str(target),hashlib.sha256(source.read_bytes()).hexdigest(),'old'))
                db.commit()
                result=ingest(db,lambda p:base/p,source,project_id=pid,kind='asset')
                self.assertTrue(result.duplicate and result.published)
                self.assertEqual(result.final_path,target)
                self.assertEqual(target.read_bytes(),b'original-bytes')
                self.assertEqual(db.execute('SELECT count(*) FROM assets').fetchone()[0],1)
            finally:db.close()

class MediaSpoolGcTests(unittest.TestCase):
    def test_only_old_unreferenced_content_is_collected(self):
        import hashlib
        import time
        from media_ingest_core import prune_unreferenced_spool
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pid='f'*32
            db=sqlite3.connect(':memory:')
            try:
                db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)')
                folder=root/pid/'.ingest_spool';folder.mkdir(parents=True)
                orphan=folder/hashlib.sha256(b'orphan').hexdigest()
                live=folder/hashlib.sha256(b'live').hexdigest()
                orphan.write_bytes(b'orphan');live.write_bytes(b'live')
                age=time.time()-90000
                os.utime(orphan,(age,age));os.utime(live,(age,age))
                db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?)',('a'*32,pid,'live',str(root/pid/'originals'/'a.png'),live.name,'t'))
                db.commit()
                result=prune_unreferenced_spool(db,lambda p:root/p,project_id=pid)
                self.assertEqual(result['deleted'],1)
                self.assertFalse(orphan.exists())
                self.assertTrue(live.exists())
            finally:db.close()
