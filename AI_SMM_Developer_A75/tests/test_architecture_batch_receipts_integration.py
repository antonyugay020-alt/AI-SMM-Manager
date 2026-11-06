import sqlite3
import tempfile
import unittest
from pathlib import Path
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime


class BatchPublicationIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=open_application_database(self.root/'manager.sqlite',self.root)
        self.addCleanup(lambda: self.db.close())
        self.db.executemany('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)',
            [('a','A','now'),('b','B','now')]);self.db.commit()
        self.core=ApplicationRuntime(self.db,lambda pid:self.root/pid)
        self.inputs={};self.outputs={}
        for pid in ('a','b'):
            (self.root/pid/'originals').mkdir(parents=True)
            (self.root/pid/'processed').mkdir()
            self.inputs[pid]=self.root/pid/'originals'/'input.png'
            self.outputs[pid]=self.root/pid/'processed'/'output.png'
            from PIL import Image
            Image.new('RGB',(2,2)).save(self.inputs[pid]);Image.new('RGB',(2,2)).save(self.outputs[pid])

    def _new(self,pid='a'):
        return self.core.batches.create(pid,[str(self.inputs[pid])],'edit',0)

    def test_no_worker_completion_without_published_result(self):
        run=self._new();claim=self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        with self.assertRaises(ValueError):self.core.batches.finish('a',run.batch_id)
        self.assertTrue(self.core.batches.interrupt(claim))
        self.assertEqual(self.core.batches.pending('a').paths,(str(self.inputs['a']),))
        with self.assertRaises(ValueError):self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=claim)
        new=self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=new)
        self.core.batches.finish('a',run.batch_id)
        self.assertIsNone(self.core.batches.pending('a'))
        self.assertEqual(self.db.execute("SELECT status,result_path FROM batch_item_receipts").fetchone(),
                         ('completed',str(self.outputs['a'])))

    def test_receipt_is_bound_to_output_bytes_and_finish_fails_closed(self):
        run=self._new();claim=self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=claim)
        receipt=self.db.execute("SELECT result_sha256,result_size FROM batch_item_receipts WHERE batch_id=?",(run.batch_id,)).fetchone()
        self.assertEqual(len(receipt[0]),64)
        self.assertGreater(receipt[1],0)
        self.outputs['a'].write_bytes(b'changed after verified publication')
        with self.assertRaisesRegex(ValueError,'modified since publication'):
            self.core.batches.finish('a',run.batch_id)
        self.assertEqual(self.db.execute('SELECT status FROM batch_runs WHERE id=?',(run.batch_id,)).fetchone()[0],'running')

    def test_removed_output_cannot_complete_batch(self):
        run=self._new();claim=self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=claim)
        self.outputs['a'].unlink()
        with self.assertRaisesRegex(ValueError,'missing or modified'):
            self.core.batches.finish('a',run.batch_id)
        self.assertEqual(self.db.execute('SELECT status FROM batch_runs WHERE id=?',(run.batch_id,)).fetchone()[0],'running')

    def test_empty_output_cannot_receive_completion_receipt(self):
        run=self._new();claim=self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.outputs['a'].write_bytes(b'')
        with self.assertRaises((ValueError,OSError)):
            self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=claim)
        self.assertEqual(self.db.execute('SELECT status FROM batch_item_receipts WHERE batch_id=?',(run.batch_id,)).fetchone()[0],'running')

    def test_atomic_publication_and_ack_rollback_on_db_failure(self):
        run=self._new();claim=self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.db.execute("""CREATE TRIGGER reject_batch_ack BEFORE UPDATE ON batch_item_receipts
            WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'batch ack failure'); END""");self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=claim)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM project_files WHERE kind='result'").fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM result_publication_events').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT status FROM batch_item_receipts').fetchone()[0],'running')
        self.db.execute('DROP TRIGGER reject_batch_ack');self.db.commit()
        self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=claim)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM project_files WHERE kind='result'").fetchone()[0],1)

    def test_batch_result_identity_cannot_replay_or_share_old_artifact(self):
        run=self._new();claim=self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=claim)
        recorded=self.db.execute('SELECT operation_id,project_id,kind FROM result_publication_events').fetchall()
        self.assertEqual(recorded,[(f'batch:{run.batch_id}:{claim.token}:image','a','image')])
        with self.assertRaises(ValueError):
            self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=claim)
        later=self._new();next_claim=self.core.batches.claim('a',later.batch_id,str(self.inputs['a']))
        with self.assertRaisesRegex(ValueError,'already registered'):
            self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=next_claim)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM project_files WHERE kind='result'").fetchone()[0],1)
        self.assertEqual(len(self.core.batches.pending('a').paths),0)

    def test_cancel_and_project_isolation(self):
        a=self._new('a');b=self._new('b')
        claim=self.core.batches.claim('a',a.batch_id,str(self.inputs['a']))
        with self.assertRaises(ValueError):self.core.batches.claim('b',a.batch_id,str(self.inputs['a']))
        self.assertTrue(self.core.batches.cancel('a',a.batch_id))
        with self.assertRaises(ValueError):self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=claim)
        self.assertEqual(self.core.batches.pending('b').batch_id,b.batch_id)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM project_files WHERE kind='result'").fetchone()[0],0)

    def test_crash_recovery_revokes_claim_and_preserves_queue(self):
        run=self._new();stale=self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.db.close();self.db=open_application_database(self.root/'manager.sqlite',self.root)
        self.core=ApplicationRuntime(self.db,lambda pid:self.root/pid)
        self.assertEqual(self.core.batches.recover('a',run.batch_id),1)
        with self.assertRaises(ValueError):self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=stale)
        self.assertEqual(self.core.batches.pending('a').total,1)
        valid=self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=valid)
        self.core.batches.finish('a',run.batch_id)

    def test_legacy_batch_requires_reconciliation(self):
        self.db.execute('''INSERT INTO batch_runs(id,project_id,prompt,engine_mode,queue_json,total,status,updated_at)
            VALUES('old','a','edit',0,'[]',5,'running','now')''');self.db.commit()
        record=self.core.batches.pending('a')
        self.assertEqual(record.batch_id,'old')
        self.assertTrue(record.blocked)
        new=self._new()
        self.assertEqual({r.batch_id for r in self.core.batches.pending_all('a')},{'old',new.batch_id})
        with self.assertRaisesRegex(ValueError,'Legacy batch'):
            self.core.batches.finish('a','old')
        self.assertEqual(self.db.execute("SELECT status FROM batch_runs WHERE id='old'").fetchone()[0],'running')

    def test_new_batch_does_not_hide_unfinished_previous_batch(self):
        old=self._new();new=self._new()
        records=self.core.batches.pending_all('a')
        self.assertEqual({r.batch_id for r in records},{old.batch_id,new.batch_id})
        self.assertEqual(self.core.batches.pending('a').batch_id,old.batch_id)
        self.core.batches.cancel('a',new.batch_id)
        self.assertEqual(self.core.batches.pending('a').batch_id,old.batch_id)

    def test_changed_original_is_fenced_before_claim_and_publication(self):
        run=self._new()
        self.inputs['a'].write_bytes(b'modified before claim')
        with self.assertRaisesRegex(ValueError,'original changed'):
            self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.assertEqual(len(self.core.batches.pending('a').paths),1)
        # A valid claim loses authority if source changes while the worker runs.
        from PIL import Image
        Image.new('RGB',(2,2)).save(self.inputs['a'])
        claim=self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.inputs['a'].write_bytes(b'modified while processing')
        with self.assertRaisesRegex(ValueError,'original changed'):
            self.core.publish_result('a','image',str(self.outputs['a']),batch_claim=claim)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM project_files WHERE kind='result'").fetchone()[0],0)

    def test_foreign_transaction_unmodified(self):
        run=self._new()
        self.db.execute("UPDATE projects SET name='uncommitted' WHERE id='a'")
        with self.assertRaises(RuntimeError):self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT name FROM projects WHERE id='a'").fetchone()[0],'A')

    def test_missing_input_is_not_dropped(self):
        run=self._new();self.inputs['a'].unlink()
        record=self.core.batches.pending('a')
        self.assertEqual(record.missing,(str(self.inputs['a']),))
        with self.assertRaises(FileNotFoundError):self.core.batches.claim('a',run.batch_id,str(self.inputs['a']))
        self.assertEqual(self.core.batches.pending('a').paths,(str(self.inputs['a']),))

if __name__=='__main__':unittest.main()
