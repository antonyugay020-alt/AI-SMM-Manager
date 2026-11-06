"""Crash/replay tests for the transactional filesystem outbox (no Qt)."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from dialogue_execution import ProjectConnection
from durable_json import atomic_write_json
from file_effect_outbox import queue_document_completion, drain, install


class DurableFileEffects(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pid = uuid4().hex
        self.artifact = uuid4().hex
        self.request = 'Создай пост и проверь его'
        self.body = 'Проверенный документ проекта'
        self.root = Path(self.temp.name) / self.pid
        (self.root / 'drafts').mkdir(parents=True)
        self.task_id = uuid4().hex
        self.receipt = self.root / 'drafts' / ('text_receipt_task_' + self.task_id + '.json')
        self.pending = self.root / 'drafts' / ('pending_text_task_' + self.task_id + '.json')
        self.sha = hashlib.sha256(self.body.encode()).hexdigest()
        self.reqsha = hashlib.sha256(self.request.encode()).hexdigest()
        self.dbfile = Path(self.temp.name) / 'project.db'
        self.db = sqlite3.connect(self.dbfile, factory=ProjectConnection)
        self.addCleanup(lambda: self.db.close())
        self.db.execute('CREATE TABLE manager_documents(id TEXT, project_id TEXT, content TEXT)')
        self.db.execute('INSERT INTO manager_documents VALUES(?,?,?)', (self.artifact, self.pid, self.body))
        install(self.db)
        self.db.commit()
        atomic_write_json(self.receipt, {'artifact_id':self.artifact, 'artifact_sha256':self.sha,
            'request_sha256':self.reqsha, 'saved_and_read_back':False})
        atomic_write_json(self.pending, {'task_id':self.task_id, 'request':self.request,
            'draft':'Незавершённая попытка'})

    def receipt_data(self):
        return {'request':self.request,'artifact_id':self.artifact,
            'artifact_sha256':self.sha,'request_sha256':self.reqsha,
            'saved_and_read_back':False,'quality_review':{'status':'PASS'}}

    def _queue(self):
        self.db.execute('BEGIN IMMEDIATE')
        queue_document_completion(self.db, project_id=self.pid, artifact_id=self.artifact,
            content=self.body, request=self.request, receipt_path=self.receipt,
            pending_path=self.pending, project_workspace=self.root,
            receipt_data=self.receipt_data())
        self.db.commit()

    def _drain(self):
        return drain(self.db, lambda _:self.root, project_id=self.pid,
                     export_document=lambda pid, artifact, content: self.assertEqual((pid,artifact,content), (self.pid,self.artifact,self.body)))

    def test_receipt_absent_before_commit_and_created_only_after_durable_write(self):
        self.receipt.unlink()
        self.db.execute('BEGIN IMMEDIATE')
        queue_document_completion(self.db, project_id=self.pid, artifact_id=self.artifact,
            content=self.body, request=self.request, receipt_path=self.receipt,
            pending_path=self.pending, project_workspace=self.root,
            receipt_data=self.receipt_data())
        self.assertFalse(self.receipt.exists())
        self.db.commit()
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self._drain()['completed'], 3)
        self.assertTrue(json.loads(self.receipt.read_text())['saved_and_read_back'])
        self.assertFalse(self.pending.exists())

    def test_rollback_leaves_absent_receipt_absent(self):
        self.receipt.unlink()
        self.db.execute('BEGIN IMMEDIATE')
        queue_document_completion(self.db, project_id=self.pid, artifact_id=self.artifact,
            content=self.body, request=self.request, receipt_path=self.receipt,
            pending_path=self.pending, project_workspace=self.root,
            receipt_data=self.receipt_data())
        self.db.rollback()
        self._drain()
        self.assertFalse(self.receipt.exists())
        self.assertTrue(self.pending.exists())

    def test_crash_after_commit_replays_both_effects(self):
        self._queue()
        self.db.close()
        self.db = sqlite3.connect(self.dbfile, factory=ProjectConnection)
        self.assertEqual(self._drain(), {'completed':3,'failed':0,'pending':0})
        self.assertTrue(json.loads(self.receipt.read_text())['saved_and_read_back'])
        self.assertFalse(self.pending.exists())

    def test_rollback_drops_effects_and_preserves_checkpoint(self):
        self.db.execute('BEGIN IMMEDIATE')
        queue_document_completion(self.db, project_id=self.pid, artifact_id=self.artifact,
            content=self.body, request=self.request, receipt_path=self.receipt,
            pending_path=self.pending, project_workspace=self.root,
            receipt_data=self.receipt_data())
        self.db.rollback()
        self.assertEqual(self._drain()['completed'],0)
        self.assertTrue(self.pending.exists())
        self.assertFalse(json.loads(self.receipt.read_text())['saved_and_read_back'])

    def test_replay_idempotent(self):
        self._queue()
        self._drain()
        self.assertEqual(self._drain(), {'completed':0,'failed':0,'pending':0})

    def test_foreign_transaction_not_committed(self):
        self._queue()
        self.db.execute('BEGIN IMMEDIATE')
        self.db.execute('UPDATE manager_documents SET content=? WHERE id=?', ('foreign', self.artifact))
        with self.assertRaises(RuntimeError):self._drain()
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertTrue(self.pending.exists())

    def test_changed_document_blocks_both_effects(self):
        self._queue()
        self.db.execute('UPDATE manager_documents SET content=? WHERE id=?', ('other',self.artifact))
        self.db.commit()
        self.assertEqual(self._drain()['failed'],3)
        self.assertTrue(self.pending.exists())
        self.assertFalse(json.loads(self.receipt.read_text())['saved_and_read_back'])

    def test_mismatched_receipt_blocks_deletion(self):
        self._queue()
        atomic_write_json(self.receipt, {'artifact_id':uuid4().hex,'artifact_sha256':self.sha,
            'request_sha256':self.reqsha,'saved_and_read_back':True})
        self.assertEqual(self._drain()['failed'],2)
        self.assertTrue(self.pending.exists())

    def test_modified_checkpoint_is_not_deleted(self):
        self._queue()
        atomic_write_json(self.pending, {'task_id':self.task_id, 'request':'new job'})
        result=self._drain()
        self.assertEqual((result['completed'],result['failed']),(2,1))
        self.assertTrue(self.pending.exists())

    def test_mismatched_task_identity_blocks_deletion(self):
        self._queue()
        atomic_write_json(self.pending, {'task_id':uuid4().hex, 'request':self.request})
        self._drain()
        self.assertTrue(self.pending.exists())

    def test_symlinked_drafts_are_blocked(self):
        other = Path(self.temp.name) / 'other'
        other.mkdir()
        (self.root / 'drafts' / 'tmp').write_text('marker')
        (self.root / 'drafts').rename(self.root / 'drafts_real')
        try:
            os.symlink(self.root / 'drafts_real', self.root / 'drafts', target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest('symlink not permitted: '+str(exc))
        with self.assertRaises(ValueError):
            self.db.execute('BEGIN IMMEDIATE')
            try:queue_document_completion(self.db, project_id=self.pid, artifact_id=self.artifact,
                content=self.body, request=self.request, receipt_path=self.receipt,
                pending_path=self.pending, project_workspace=self.root,
            receipt_data=self.receipt_data())
            finally:self.db.rollback()

    def test_replay_symlink_rejected_without_outside_effects(self):
        self._queue()
        (self.root / 'drafts').rename(self.root / 'drafts_real')
        try:os.symlink(self.root / 'drafts_real', self.root / 'drafts', target_is_directory=True)
        except (NotImplementedError,OSError) as exc:self.skipTest(str(exc))
        self.assertEqual(self._drain()['failed'],3)
        self.assertTrue((self.root / 'drafts_real' / self.pending.name).exists())

    def test_crash_during_receipt_update_retries(self):
        self._queue()
        with patch('file_effect_outbox.atomic_write_json', side_effect=OSError('simulated crash')):
            self.assertEqual(self._drain()['failed'],2)
        self.assertTrue(self.pending.exists())
        self.assertEqual(self._drain()['completed'],2)
        self.assertFalse(self.pending.exists())

    def test_bad_path_not_serialized(self):
        bad = Path(self.temp.name) / 'outside' / self.receipt.name
        self.db.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(ValueError):
                queue_document_completion(self.db, project_id=self.pid, artifact_id=self.artifact,
                    content=self.body, request=self.request, receipt_path=bad,
                    pending_path=self.pending, project_workspace=self.root,
            receipt_data=self.receipt_data())
        finally:self.db.rollback()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_file_effect_outbox').fetchone()[0],0)

    def test_wrong_project_cannot_claim_document(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(ValueError):
                queue_document_completion(self.db, project_id=uuid4().hex, artifact_id=self.artifact,
                    content=self.body, request=self.request, receipt_path=self.receipt,
                    pending_path=self.pending, project_workspace=self.root,
            receipt_data=self.receipt_data())
        finally:self.db.rollback()

if __name__=='__main__':unittest.main()
