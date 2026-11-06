import sqlite3
import tempfile
import unittest
from pathlib import Path
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime

class PublicationIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=open_application_database(self.root/'manager.sqlite',self.root)
        self.addCleanup(self.db.close)
        self.db.executemany("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)",[('a','A','now'),('b','B','now')])
        self.db.commit()
        self.runtime=ApplicationRuntime(self.db,lambda pid:self.root/pid)
        self.service=self.runtime.publication_queue
        self.assets={}
        for pid in ('a','b'):
            folder=self.root/pid/'ready';folder.mkdir(parents=True)
            self.assets[pid]=folder/'same.png';self.assets[pid].write_bytes(pid.encode())

    def test_project_reviews_jobs_and_rules_are_isolated(self):
        a=self.assets['a'];b=self.assets['b']
        self.service.review_media('a',[str(a)],'Одобрен')
        self.assertEqual(self.service.media_status('b',str(b)),'Ожидает проверки')
        ids=self.service.enqueue('a',[str(a)],'Instagram')
        self.assertEqual(len(ids),1)
        self.assertEqual(self.service.jobs('b'),())
        self.assertEqual(self.service.job_paths('a',list(ids)),(str(a),))
        self.assertFalse(self.service.schedule('b',ids[0],'По графику блока'))
        self.assertFalse(self.service.delete('b',ids[0]))
        self.assertTrue(self.service.schedule('a',ids[0],'Индивидуально','2027-01-02 10:45'))
        self.assertEqual(self.service.jobs('a')[0][4],'2027-01-02 10:45')
        self.service.save_block_rule('a','Instagram','Еженедельно','09:00')
        self.assertEqual(self.service.block_rule('a','Instagram'),('Еженедельно','09:00'))
        self.assertIsNone(self.service.block_rule('b','Instagram'))
        self.assertTrue(self.service.delete('a',ids[0]))
        self.assertTrue(a.is_file())

    def test_unapproved_media_blocks_entire_batch_and_no_partial_jobs(self):
        a=self.assets['a'];extra=a.parent/'other.png';extra.write_bytes(b'x')
        self.service.review_media('a',[str(a)],'Одобрен')
        with self.assertRaises(ValueError):
            self.service.enqueue('a',[str(a),str(extra)],'Instagram')
        self.assertEqual(self.service.jobs('a'),())

    def test_trigger_failure_rolls_back_batch(self):
        a=self.assets['a'];extra=a.parent/'other.png';extra.write_bytes(b'x')
        self.service.review_media('a',[str(a),str(extra)],'Одобрен')
        self.db.execute('''CREATE TRIGGER fail_second_job BEFORE INSERT ON publication_jobs
            WHEN NEW.media_path LIKE '%other.png' BEGIN SELECT RAISE(ABORT,'fail'); END''')
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.enqueue('a',[str(a),str(extra)],'Instagram')
        self.assertEqual(self.service.jobs('a'),())

    def test_review_downgrade_does_not_destroy_jobs(self):
        a=self.assets['a'];self.service.review_media('a',[str(a)],'Одобрен')
        ident=self.service.enqueue('a',[str(a)],'Instagram')[0]
        self.service.review_media('a',[str(a)],'Ожидает проверки')
        self.assertEqual(self.service.jobs('a')[0][5],'Черновик')
        self.assertEqual(self.service.jobs('a')[0][0],ident)

    def test_symlink_and_foreign_file_rejected(self):
        a=self.assets['a'];b=self.assets['b']
        with self.assertRaises(ValueError):
            self.service.review_media('a',[str(b)],'Одобрен')
        link=a.parent/'linked.png'
        try:link.symlink_to(b)
        except (OSError,NotImplementedError):return
        with self.assertRaises(ValueError):
            self.service.review_media('a',[str(link)],'Одобрен')

    def test_rejection_move_then_failed_ack_recovers_after_restart(self):
        media=self.assets['a']
        self.service.review_media('a',[str(media)],'Одобрен')
        self.service.enqueue('a',[str(media)],'Instagram')
        self.db.execute("""CREATE TRIGGER reject_ack BEFORE UPDATE ON publication_media_rejections
            WHEN NEW.state='done' BEGIN SELECT RAISE(ABORT,'injected acknowledgment crash'); END""")
        self.db.commit()
        result=self.service.reject_media('a',[str(media)])
        self.assertEqual(len(result['failed']),1)
        self.assertFalse(media.exists())
        row=self.db.execute("SELECT destination_path,state FROM publication_media_rejections").fetchone()
        self.assertEqual(row[1],'pending')
        self.assertTrue(Path(row[0]).is_file())
        self.assertEqual(self.service.jobs('a'),())
        with self.assertRaises(ValueError):self.service.enqueue('a',[str(media)],'Instagram')
        self.db.execute('DROP TRIGGER reject_ack');self.db.commit()
        self.db.close()
        self.db=open_application_database(self.root/'manager.sqlite',self.root)
        self.service=ApplicationRuntime(self.db,lambda pid:self.root/pid).publication_queue
        again=self.service.recover_rejections(project_id='a')
        self.assertEqual(len(again['done']),1)
        self.assertEqual(self.db.execute("SELECT state FROM publication_media_rejections").fetchone()[0],'done')
        self.assertEqual(self.db.execute('SELECT count(*) FROM publication_jobs').fetchone()[0],0)
        self.assertTrue(Path(row[0]).is_file())

    def test_unrelated_project_survives_rejected_media(self):
        a=self.assets['a'];b=self.assets['b']
        self.service.review_media('a',[str(a)],'Одобрен')
        self.service.review_media('b',[str(b)],'Одобрен')
        self.service.enqueue('a',[str(a)],'Instagram')
        self.service.enqueue('b',[str(b)],'Instagram')
        result=self.service.reject_media('a',[str(a)])
        self.assertEqual(len(result['done']),1)
        self.assertEqual(len(self.service.jobs('a')),0)
        self.assertEqual(len(self.service.jobs('b')),1)
        self.assertTrue(b.is_file())

    def test_rejection_refuses_mutated_media_and_never_deletes_queue(self):
        media=self.assets['a']
        self.service.review_media('a',[str(media)],'Одобрен')
        self.service.enqueue('a',[str(media)],'Instagram')
        # Force the replay to defer while retaining a durable intent.
        from unittest.mock import patch
        with patch('publication_runtime.os.replace',side_effect=OSError('disk busy')):
            result=self.service.reject_media('a',[str(media)])
        self.assertEqual(len(result['failed']),1)
        media.write_bytes(b'mutated')
        again=self.service.recover_rejections(project_id='a')
        self.assertEqual(len(again['failed']),1)
        self.assertTrue(media.is_file())
        self.assertEqual(self.db.execute('SELECT count(*) FROM publication_jobs').fetchone()[0],1)
        self.assertEqual(self.service.jobs('a'),())

    def test_import_ready_media_survives_reopen_and_preserves_original(self):
        external=self.root/'external.png';external.write_bytes(b'photo-original')
        imported=self.service.import_ready_sources('a',[str(external)])
        self.assertEqual(len(imported['done']),1)
        final=Path(imported['done'][0]);self.assertTrue(final.is_file())
        self.assertEqual(final.read_bytes(),external.read_bytes())
        self.assertEqual(self.service.media_status('a',str(final)),'Ожидает проверки')
        self.db.close()
        fresh=open_application_database(self.root/'manager.sqlite',self.root)
        self.assertEqual(fresh.execute('SELECT state FROM publication_ready_ingest').fetchone()[0],'done')
        fresh.close()
        self.assertTrue(external.is_file())

    def test_import_ready_media_replays_after_ack_failure(self):
        external=self.root/'external.png';external.write_bytes(b'original')
        self.db.execute("""CREATE TRIGGER reject_ready_ack BEFORE UPDATE ON publication_ready_ingest
            WHEN NEW.state='done' BEGIN SELECT RAISE(ABORT,'failed ack'); END""")
        self.db.commit()
        result=self.service.import_ready_sources('a',[str(external)])
        self.assertEqual(len(result['failed']),1)
        row=self.db.execute('SELECT spool_path,destination_path,state FROM publication_ready_ingest').fetchone()
        self.assertEqual(row[2],'pending');self.assertTrue(Path(row[1]).is_file())
        self.assertFalse(Path(row[0]).exists())
        self.db.execute('DROP TRIGGER reject_ready_ack');self.db.commit()
        recovered=self.service.recover_ready_ingest(project_id='a')
        self.assertEqual(len(recovered['done']),1)
        self.assertEqual(self.service.media_status('a',row[1]),'Ожидает проверки')
        self.assertEqual(Path(row[1]).read_bytes(),external.read_bytes())

    def test_import_staging_failure_does_not_claim_ready_media(self):
        external=self.root/'bad.png';external.write_bytes(b'orig')
        from unittest.mock import patch
        with patch('publication_runtime.os.replace',side_effect=OSError('locked')):
            result=self.service.import_ready_sources('a',[str(external)])
        self.assertEqual(len(result['failed']),1)
        row=self.db.execute('SELECT spool_path,destination_path,state FROM publication_ready_ingest').fetchone()
        self.assertEqual(row[2],'pending');self.assertTrue(Path(row[0]).is_file())
        self.assertFalse(Path(row[1]).exists())
        result=self.service.recover_ready_ingest(project_id='a')
        self.assertEqual(len(result['done']),1)
        self.assertTrue(Path(row[1]).is_file())

    def test_unowned_external_symlink_is_rejected_without_writing_database(self):
        external=self.root/'real.png';external.write_bytes(b'original')
        link=self.root/'fake.png'
        try:link.symlink_to(external)
        except (OSError,NotImplementedError):return
        result=self.service.import_ready_sources('a',[str(link)])
        self.assertEqual(len(result['failed']),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM publication_ready_ingest').fetchone()[0],0)

    def test_external_transaction_not_committed(self):
        a=self.assets['a'];self.db.execute("UPDATE projects SET name='changed' WHERE id='a'")
        with self.assertRaises(RuntimeError):self.service.review_media('a',[str(a)],'Одобрен')
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT name FROM projects WHERE id='a'").fetchone()[0],'A')
