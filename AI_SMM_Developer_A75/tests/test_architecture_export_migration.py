"""Data-safe v1 -> v2 outbox migration and export-before-receipt invariant."""
import hashlib
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from file_effect_outbox import install, queue_document_completion, drain


class ExportMigration(unittest.TestCase):
    def test_existing_outbox_rows_survive_migration(self):
        with TemporaryDirectory() as temp:
            db = sqlite3.connect(Path(temp) / 'data.sqlite3')
            db.execute('''CREATE TABLE ai_file_effect_outbox (
                id TEXT PRIMARY KEY,project_id TEXT NOT NULL,artifact_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,request_sha256 TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('verify_receipt','clear_pending')),
                receipt_name TEXT NOT NULL,pending_name TEXT NOT NULL,receipt_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','done')),
                attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL)''')
            db.execute('''INSERT INTO ai_file_effect_outbox VALUES
                ('identity','p','a','sha','req','verify_receipt','receipt','pending','{}',
                 'pending',4,'old failure','2026-11-02')''')
            db.commit()
            install(db)
            db.commit()
            self.assertEqual(db.execute('SELECT id,state,attempts,last_error FROM ai_file_effect_outbox').fetchone(),
                             ('identity','pending',4,'old failure'))
            schema = db.execute("SELECT sql FROM sqlite_master WHERE name='ai_file_effect_outbox'").fetchone()[0]
            self.assertIn("'export_document'",schema)
            install(db)
            self.assertEqual(db.execute('SELECT count(*) FROM ai_file_effect_outbox').fetchone()[0],1)
            db.close()

    def test_exporter_failure_does_not_acknowledge_task(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve(); (root/'drafts').mkdir()
            db=sqlite3.connect(':memory:'); db.execute('CREATE TABLE manager_documents (id TEXT,project_id TEXT,content TEXT)')
            pid=uuid4().hex; art=uuid4().hex; text='verified'; req='create verified text'
            db.execute('INSERT INTO manager_documents VALUES (?,?,?)',(art,pid,text))
            r=root/'drafts'/('text_receipt_task_'+art+'.json'); p=root/'drafts'/('pending_text_task_'+art+'.json')
            p.write_text(json.dumps({'task_id':art,'request':req}),encoding='utf8')
            data=dict(artifact_id=art, artifact_sha256=hashlib.sha256(text.encode()).hexdigest(),
                      request_sha256=hashlib.sha256(req.encode()).hexdigest(),request=req,saved_and_read_back=False)
            queue_document_completion(db,project_id=pid,artifact_id=art,content=text,request=req,
                                      receipt_path=r,pending_path=p,project_workspace=root,receipt_data=data)
            db.commit()
            def broken(*args): raise OSError('disk full')
            report=drain(db,lambda _:root,export_document=broken)
            self.assertEqual(report['pending'],3)
            self.assertTrue(p.exists())
            self.assertFalse(r.exists())
            self.assertEqual(drain(db,lambda _:root,export_document=lambda *_:None)['completed'],3)
            self.assertFalse(p.exists())
            db.close()

if __name__=='__main__': unittest.main()
