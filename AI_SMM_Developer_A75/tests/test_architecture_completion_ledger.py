"""End-to-end SQLite completion transaction, independent of Qt."""
import hashlib
import sqlite3
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from dialogue_execution import ProjectConnection
from completion_ledger import finalize_document
from file_effect_outbox import queue_document_completion,drain
from task_registry import ensure_registry


class CompletionLedgerTests(unittest.TestCase):
    def setUp(self):
        t=TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name).resolve();(self.root/'drafts').mkdir()
        self.db=sqlite3.connect(':memory:',factory=ProjectConnection)
        self.addCleanup(self.db.close)
        self.pid=uuid4().hex;self.art=uuid4().hex;self.task=uuid4().hex
        self.req='Create 16 verified posts';self.content='16 independently checked posts'
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE manager_documents(id TEXT,project_id TEXT,kind TEXT,title TEXT,content TEXT)')
        self.db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT,role TEXT,content TEXT,created_at TEXT)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT,question TEXT,revision_id TEXT)')
        self.db.execute('INSERT INTO projects VALUES(?)',(self.pid,))
        self.db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?)',(self.art,self.pid,'calendar','posts',self.content))
        self.db.execute('INSERT INTO ai_pending_tasks VALUES(?,?,?,?)',(self.pid,self.req,'Working',None))
        ensure_registry(self.db)
        self.task=self.db.execute("SELECT task_id FROM ai_task_registry WHERE project_id=? AND status='active'",(self.pid,)).fetchone()[0]
        self.db.commit()
        self.receipt=self.root/'drafts'/('text_receipt_task_'+self.task+'.json')
        self.pending=self.root/'drafts'/('pending_text_task_'+self.task+'.json')
        self.pending.write_text(json.dumps({'request':self.req,'task_id':self.task}), encoding='utf-8')
        data={'request':self.req,'request_sha256':hashlib.sha256(self.req.encode()).hexdigest(),
              'artifact_id':self.art,'artifact_sha256':hashlib.sha256(self.content.encode()).hexdigest(),
              'saved_and_read_back':False}
        self.db.execute('BEGIN IMMEDIATE')
        queue_document_completion(self.db,project_id=self.pid,artifact_id=self.art,content=self.content,
                                  request=self.req,receipt_path=self.receipt,pending_path=self.pending,
                                  project_workspace=self.root,receipt_data=data)
        self.db.commit()

    def complete_exports(self):
        report=drain(self.db,lambda _:self.root,export_document=lambda *_:None)
        self.assertEqual(report['pending'],0)

    def finalize(self):
        return finalize_document(self.db,project_id=self.pid,artifact_id=self.art,
                                 request=self.req,task_id=self.task,notice='Document verified')

    def test_cannot_complete_before_export(self):
        with self.assertRaisesRegex(ValueError,'not yet verified'):
            self.finalize()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_pending_tasks').fetchone()[0],1)

    def test_completed_once_with_task_journal_and_notice(self):
        self.complete_exports()
        self.assertTrue(self.finalize())
        self.assertFalse(self.finalize())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_completed_artifacts').fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ai_execution_journal WHERE status='saved'").fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],1)
        self.assertFalse(self.db.execute('SELECT 1 FROM ai_pending_tasks').fetchone())
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(self.task,)).fetchone()[0],'completed')

    def test_failure_mid_finalization_rolls_everything_back(self):
        self.complete_exports()
        with patch('stage57_core.receipt',side_effect=OSError('forced journal failure')):
            with self.assertRaises(OSError): self.finalize()
        self.assertFalse(self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_completed_artifacts'").fetchone())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_pending_tasks').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(self.task,)).fetchone()[0],'active')
        self.assertTrue(self.finalize())

    def test_unrelated_current_task_cannot_be_closed(self):
        self.complete_exports()
        self.db.execute('UPDATE ai_pending_tasks SET request=? WHERE project_id=?',('Another task',self.pid))
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'Another project task'):
            self.finalize()
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks').fetchone()[0],'Another task')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

    def test_stale_identity_same_prompt_rejected(self):
        self.complete_exports()
        with self.assertRaises(ValueError):
            finalize_document(self.db,project_id=self.pid,artifact_id=self.art,
                              request=self.req,task_id=uuid4().hex,notice='Document verified')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

if __name__=='__main__':unittest.main()
