import sqlite3
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime

class DurableBatchDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=open_application_database(self.root/'db.sqlite',self.root)
        self.addCleanup(lambda:self.db.close())
        self.db.execute("INSERT INTO projects(id,name,created_at) VALUES('a','A','now')")
        self.db.execute("INSERT INTO projects(id,name,created_at) VALUES('b','B','now')")
        self.db.commit()
        for pid in ('a','b'):
            for sub in ('originals','processed'):(self.root/pid/sub).mkdir(parents=True)
        self.core=ApplicationRuntime(self.db,lambda pid:self.root/pid)

    def _source(self,pid,name):
        path=self.root/pid/'originals'/name
        Image.new('RGB',(2,2)).save(path)
        return str(path)

    def _output(self,pid,name):
        path=self.root/pid/'processed'/name
        Image.new('RGB',(2,2)).save(path)
        return str(path)

    def test_dispatch_persists_order_across_restart(self):
        first=self._source('a','1.png');second=self._source('a','2.png')
        run=self.core.batches.create('a',[first,second],'edit',0)
        claim=self.core.batches.advance('a',run.batch_id)
        self.assertEqual(claim.source_path,first)
        with self.assertRaisesRegex(ValueError,'already claimed'):
            self.core.batches.advance('a',run.batch_id)
        self.core.publish_result('a','image',self._output('a','one.png'),batch_claim=claim)
        self.db.close()
        self.db=open_application_database(self.root/'db.sqlite',self.root)
        self.core=ApplicationRuntime(self.db,lambda pid:self.root/pid)
        next_claim=self.core.batches.advance('a',run.batch_id)
        self.assertEqual(next_claim.source_path,second)
        self.core.publish_result('a','image',self._output('a','two.png'),batch_claim=next_claim)
        self.assertIsNone(self.core.batches.advance('a',run.batch_id))
        self.assertIsNone(self.core.batches.pending('a'))
        self.assertEqual(self.db.execute('SELECT status FROM batch_runs WHERE id=?',(run.batch_id,)).fetchone(),('completed',))

    def test_failed_first_item_blocks_later_items_without_losing_them(self):
        first=self._source('a','1.png');second=self._source('a','2.png')
        run=self.core.batches.create('a',[first,second],'edit',0)
        claim=self.core.batches.advance('a',run.batch_id)
        self.assertTrue(self.core.batches.interrupt(claim))
        retry=self.core.batches.advance('a',run.batch_id)
        self.assertEqual(retry.source_path,first)
        self.assertNotEqual(retry.token,claim.token)
        self.assertEqual(self.core.batches.pending('a').paths,(first,second))

    def test_atomic_finish_rejects_tampered_output(self):
        path=self._source('a','1.png')
        run=self.core.batches.create('a',[path],'edit',0)
        claim=self.core.batches.advance('a',run.batch_id)
        result=self._output('a','one.png')
        self.core.publish_result('a','image',result,batch_claim=claim)
        Path(result).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'modified since publication'):
            self.core.batches.advance('a',run.batch_id)
        self.assertEqual(self.db.execute('SELECT status FROM batch_runs WHERE id=?',(run.batch_id,)).fetchone(),('running',))

    def test_other_project_cannot_claim_or_finish(self):
        path=self._source('a','1.png');run=self.core.batches.create('a',[path],'edit',0)
        with self.assertRaises(ValueError):self.core.batches.advance('b',run.batch_id)
        self.assertEqual(self.core.batches.advance('a',run.batch_id).project_id,'a')

    def test_unrelated_transaction_is_never_committed(self):
        path=self._source('a','1.png');run=self.core.batches.create('a',[path],'edit',0)
        self.db.execute("UPDATE projects SET name='temporary' WHERE id='a'")
        with self.assertRaises(RuntimeError):self.core.batches.advance('a',run.batch_id)
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT name FROM projects WHERE id='a'").fetchone(),('A',))
