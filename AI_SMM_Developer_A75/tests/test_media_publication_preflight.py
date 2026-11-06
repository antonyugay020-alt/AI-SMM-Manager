"""Atomic publication with out-of-transaction media streaming."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import project_result_registry
import result_publication_runtime
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime

class PublicationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=open_application_database(self.root/'db.sqlite3',self.root)
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO projects(id,name,created_at) VALUES('a','A','now')");self.db.commit()
        (self.root/'a'/'originals').mkdir(parents=True)
        (self.root/'a'/'processed').mkdir()
        self.source=self.root/'a'/'originals'/'input.png'
        self.output=self.root/'a'/'processed'/'output.png'
        Image.new('RGB',(8,8)).save(self.source)
        Image.new('RGB',(8,8)).save(self.output)
        self.core=ApplicationRuntime(self.db,lambda p:self.root/p)

    def test_media_stream_and_decode_before_sqlite_write_lock(self):
        original=project_result_registry.capture
        def checked(*args,**kwargs):
            self.assertFalse(self.db.in_transaction)
            return original(*args,**kwargs)
        with patch.object(project_result_registry,'capture',side_effect=checked), \
             patch.object(result_publication_runtime,'_media_digest',side_effect=AssertionError('in-transaction full hash')):
            self.core.publish_result('a','image',str(self.output),operation_id='image:a')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],1)

    def test_batch_receipt_uses_preflight_evidence(self):
        run=self.core.batches.create('a',[str(self.source)],'edit',0)
        claim=self.core.batches.claim('a',run.batch_id,str(self.source))
        import batch_runtime
        with patch.object(batch_runtime,'_fingerprint',side_effect=AssertionError('full hash in publication')):
            self.core.publish_result('a','image',str(self.output),batch_claim=claim)
        self.assertEqual(self.db.execute('SELECT status FROM batch_item_receipts').fetchone(),('completed',))

    def test_mutation_inside_savepoint_rolls_back_all_authorities(self):
        run=self.core.batches.create('a',[str(self.source)],'edit',0)
        claim=self.core.batches.claim('a',run.batch_id,str(self.source))
        registry=self.core.publications.registry
        original=registry.admit_prepared_media
        def mutate(*args,**kwargs):
            result=original(*args,**kwargs)
            Image.new('RGB',(16,16)).save(self.output)
            return result
        with patch.object(registry,'admit_prepared_media',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'changed during publication'):
                self.core.publish_result('a','image',str(self.output),batch_claim=claim)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM result_publication_events').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT status FROM batch_item_receipts').fetchone(),('running',))
        self.assertFalse(self.db.in_transaction)

    def test_replay_after_restart(self):
        first=self.core.publish_result('a','image',str(self.output),operation_id='image:a')
        reopened=sqlite3.connect(self.root/'db.sqlite3');self.addCleanup(reopened.close)
        second=ApplicationRuntime(reopened,lambda p:self.root/p).publish_result(
            'a','image',str(self.output),operation_id='image:a')
        self.assertEqual(first,second)
