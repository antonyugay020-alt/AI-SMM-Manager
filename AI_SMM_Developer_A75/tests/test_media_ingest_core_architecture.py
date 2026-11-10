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

    def test_scoped_replay_does_not_count_or_publish_other_project(self):
        import media_ingest_core
        with patch.object(media_ingest_core,'replay',return_value={'completed':0,'failed':0,'pending':1}):
            a=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
            b=ingest(self.db,self.workspace,self.src,project_id=self.p2,kind='asset')
        self.assertFalse(a.published or b.published)
        result=replay(self.db,self.workspace,project_id=self.p1)
        self.assertEqual(result,{'completed':1,'failed':0,'pending':0})
        self.assertTrue(a.final_path.is_file())
        self.assertFalse(b.final_path.exists())
        self.assertEqual(self.db.execute(
            "SELECT state FROM media_ingest_jobs WHERE project_id=?",(self.p2,)
        ).fetchone()[0],'pending')
        self.assertEqual(replay(self.db,self.workspace,project_id=self.p2)['completed'],1)

    def test_global_replay_rejects_orphaned_project_receipt(self):
        import media_ingest_core
        with patch.object(media_ingest_core,'replay',return_value={'completed':0,'failed':0,'pending':1}):
            receipt=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        self.db.execute('DELETE FROM projects WHERE id=?',(self.p1,))
        self.db.commit()
        result=replay(self.db,self.workspace)
        self.assertEqual(result,{'completed':0,'failed':1,'pending':1})
        self.assertFalse(receipt.final_path.exists())
        self.assertEqual(self.db.execute(
            "SELECT state FROM media_ingest_jobs WHERE id=?",(receipt.item_id,)
        ).fetchone()[0],'pending')

    def test_scoped_replay_unknown_owner_fails_before_filesystem_io(self):
        unknown='c'*32
        with self.assertRaisesRegex(ValueError,'Project does not exist'):
            replay(self.db,self.workspace,project_id=unknown)
        self.assertFalse(self.workspace(unknown).exists())
        with self.assertRaises(ValueError):
            replay(self.db,self.workspace,project_id='../../other')

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

    def test_published_assets_never_share_inode_with_recovery_spool(self):
        original=self.src.read_bytes()
        receipt=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        self.assertTrue(receipt.published)
        spool=self.workspace(self.p1)/'.ingest_spool'/(_digest(self.src)[0])
        self.assertTrue(spool.is_file())
        self.assertFalse(os.path.samefile(spool,receipt.final_path))
        receipt.final_path.write_bytes(b'changed-by-user')
        self.assertEqual(spool.read_bytes(),original)
        self.assertEqual(_digest(spool),(_digest(self.src)[0],len(original)))

    def test_reference_and_result_publication_keep_independent_recovery_bytes(self):
        original=self.src.read_bytes()
        for kind in ('reference','result'):
            with self.subTest(kind=kind):
                receipt=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind=kind)
                self.assertTrue(receipt.published)
                spool=self.workspace(self.p1)/'.ingest_spool'/(_digest(self.src)[0])
                self.assertFalse(os.path.samefile(spool,receipt.final_path))
                receipt.final_path.write_bytes(b'user-edited-'+kind.encode())
                self.assertEqual(spool.read_bytes(),original)

    def test_failed_independent_copy_leaves_pending_intent_and_no_temp_files(self):
        import media_ingest_core
        real_link=media_ingest_core.os.link
        def fail_publish(src,dst,*args,**kwargs):
            if Path(src).name.startswith('.publish-'):
                raise OSError('synthetic publication failure')
            return real_link(src,dst,*args,**kwargs)
        with patch.object(media_ingest_core.os,'link',side_effect=fail_publish):
            receipt=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        self.assertFalse(receipt.published)
        self.assertFalse(receipt.final_path.exists())
        self.assertEqual(list(receipt.final_path.parent.glob('.publish-*')),[])
        self.assertEqual(self.db.execute('SELECT state FROM media_ingest_jobs').fetchone()[0],'pending')
        self.assertEqual(replay(self.db,self.workspace)['completed'],1)
        self.assertTrue(receipt.final_path.is_file())

    def test_concurrent_conflicting_publication_does_not_replace_existing_bytes(self):
        import media_ingest_core
        real_link=media_ingest_core.os.link
        def competing_writer(src,dst,*args,**kwargs):
            if Path(src).name.startswith('.publish-'):
                Path(dst).write_bytes(b'other-writer')
                raise FileExistsError('concurrent publication')
            return real_link(src,dst,*args,**kwargs)
        with patch.object(media_ingest_core.os,'link',side_effect=competing_writer):
            receipt=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        self.assertFalse(receipt.published)
        self.assertEqual(receipt.final_path.read_bytes(),b'other-writer')
        self.assertEqual(list(receipt.final_path.parent.glob('.publish-*')),[])
        self.assertEqual(self.db.execute('SELECT state FROM media_ingest_jobs').fetchone()[0],'pending')


    def test_video_result_is_project_owned_and_spool_is_independent(self):
        clip=self.base/'clip.mp4'
        clip.write_bytes(b'video-bytes-for-ingest-contract')
        receipt=ingest(self.db,self.workspace,clip,project_id=self.p2,kind='video')
        self.assertTrue(receipt.published)
        row=self.db.execute('SELECT project_id,kind,path FROM project_files WHERE id=?',(receipt.item_id,)).fetchone()
        self.assertEqual(row,(self.p2,'video',str(receipt.final_path)))
        spool=self.workspace(self.p2)/'.ingest_spool'/_digest(clip)[0]
        self.assertFalse(os.path.samefile(spool,receipt.final_path))
        receipt.final_path.write_bytes(b'user-edited-video')
        self.assertEqual(spool.read_bytes(),clip.read_bytes())

    def test_missing_or_corrupt_spool_is_not_accepted(self):
        import media_ingest_core
        with patch.object(media_ingest_core,'replay',return_value={'completed':0,'failed':0,'pending':1}):
            receipt=ingest(self.db,self.workspace,self.src,project_id=self.p1,kind='asset')
        spool=list((self.workspace(self.p1)/'.ingest_spool').iterdir())
        # Remove only content blobs; the permanent OS lock inode must survive.
        for path in spool:
            if len(path.name)==64 and all(c in '0123456789abcdef' for c in path.name):
                path.unlink()
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
    def test_deleted_project_cannot_trigger_spool_cleanup(self):
        import hashlib
        from media_ingest_core import prune_unreferenced_spool
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pid='f'*32
            folder=root/pid/'.ingest_spool';folder.mkdir(parents=True)
            orphan=folder/hashlib.sha256(b'old').hexdigest()
            orphan.write_bytes(b'old')
            db=sqlite3.connect(':memory:')
            try:
                db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
                db.commit()
                with self.assertRaisesRegex(ValueError,'Project does not exist'):
                    prune_unreferenced_spool(db,lambda p:root/p,project_id=pid)
                self.assertEqual(orphan.read_bytes(),b'old')
            finally:db.close()

    def test_only_old_unreferenced_content_is_collected(self):
        import hashlib
        import time
        from media_ingest_core import prune_unreferenced_spool
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pid='f'*32
            db=sqlite3.connect(':memory:')
            try:
                db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)')
                db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
                db.execute('INSERT INTO projects VALUES(?)',(pid,))
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


class MediaStageSourceRaceTests(unittest.TestCase):
    """Source swaps and mid-copy writes cannot become durable project media."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'project';self.root.mkdir()
        self.source=Path(self.tmp.name)/'source.png'
        self.source.write_bytes(b'original-media-bytes')

    def test_stable_source_stages(self):
        from media_ingest_core import stage_source
        sha,length=stage_source(self.root,self.source)
        self.assertEqual(length,len(b'original-media-bytes'))
        self.assertEqual((self.root/'.ingest_spool'/sha).read_bytes(),b'original-media-bytes')

    def test_replacement_before_open_is_rejected(self):
        import media_ingest_core
        real=media_ingest_core.open_source
        def replace(path,*args,**kwargs):
            if Path(path)==self.source:
                self.source.unlink();self.source.write_bytes(b'replacement')
            return real(path,*args,**kwargs)
        with patch.object(media_ingest_core,'open_source',side_effect=replace):
            with self.assertRaisesRegex(ValueError,'changed'):
                media_ingest_core.stage_source(self.root,self.source)
        self.assertEqual(list((self.root/'.ingest_spool').iterdir()),[])

    def test_mutation_after_copy_is_rejected(self):
        import media_ingest_core
        real=media_ingest_core.descriptor_identity
        calls=[0]
        def mutate(fd,path=None):
            calls[0]+=1
            identity=real(fd,path)
            if calls[0]==2:return (*identity[:3],identity[3]+1,identity[4])
            return identity
        with patch.object(media_ingest_core,'descriptor_identity',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'changed'):
                media_ingest_core.stage_source(self.root,self.source)
        self.assertEqual(list((self.root/'.ingest_spool').iterdir()),[])



class AtomicMediaSchemaMigrationTests(unittest.TestCase):
    """Old media receipts remain durable if an upgrade is interrupted."""
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute("""CREATE TABLE media_ingest_jobs (
            id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL
            CHECK(kind IN ('asset','reference')),source_sha256 TEXT NOT NULL,
            source_size INTEGER NOT NULL,filename TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('pending','done')),
            attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL)""")
        self.db.execute("""INSERT INTO media_ingest_jobs
            (id,project_id,kind,source_sha256,source_size,filename,state,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            ('a'*32,'b'*32,'asset','c'*64,4,'a'*32+'.png','pending','old'))
        self.db.commit()

    def test_upgrade_preserves_receipt_and_accepts_video(self):
        from media_ingest_core import install
        install(self.db)
        self.assertEqual(self.db.execute('SELECT id,kind,state FROM media_ingest_jobs').fetchone(),
                         ('a'*32,'asset','pending'))
        self.db.execute("""INSERT INTO media_ingest_jobs
            (id,project_id,kind,source_sha256,source_size,filename,state,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            ('d'*32,'b'*32,'video','e'*64,4,'d'*32+'.mp4','pending','now'))
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT kind FROM media_ingest_jobs WHERE id=?",('d'*32,)).fetchone()[0],'video')

    def test_failed_ddl_upgrade_rolls_back_without_orphan_table(self):
        from media_ingest_core import install
        def deny_drop(action,table,*_):
            if action==sqlite3.SQLITE_DROP_TABLE and table=='media_ingest_jobs':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.db.set_authorizer(deny_drop)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                install(self.db)
        finally:
            self.db.set_authorizer(None)
        self.assertEqual(self.db.execute('SELECT id,kind FROM media_ingest_jobs').fetchone(),
                         ('a'*32,'asset'))
        self.assertIsNone(self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_ingest_jobs_v2'").fetchone())
        self.assertNotIn("'video'",self.db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='media_ingest_jobs'").fetchone()[0])

    def test_nested_caller_transaction_can_roll_back_schema_upgrade(self):
        from media_ingest_core import install
        self.db.execute('CREATE TABLE unrelated(message TEXT)')
        self.db.commit()
        self.db.execute('BEGIN IMMEDIATE')
        self.db.execute("INSERT INTO unrelated VALUES('uncommitted')")
        install(self.db)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM unrelated').fetchone()[0],0)
        self.assertNotIn("'video'",self.db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='media_ingest_jobs'").fetchone()[0])
        self.assertEqual(self.db.execute('SELECT id FROM media_ingest_jobs').fetchone()[0],'a'*32)
