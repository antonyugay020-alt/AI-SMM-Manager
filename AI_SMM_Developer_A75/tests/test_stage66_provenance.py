import hashlib
import json
import sqlite3
import unittest
from task_artifacts import verified_document

class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE manager_documents (id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, content TEXT, updated_at TEXT)')
        self.db.execute('CREATE TABLE ai_execution_journal (id INTEGER PRIMARY KEY, project_id TEXT, request TEXT, artifact_kind TEXT, artifact_sha256 TEXT, status TEXT, details TEXT)')
        self.db.execute('INSERT INTO manager_documents VALUES (?,?,?,?,?)',('doc','p','strategy','Старая стратегия','2026-01-01'))
    def tearDown(self):
        self.db.close()
    def receipt(self,request,content,status='saved'):
        self.db.execute('INSERT INTO ai_execution_journal(project_id,request,artifact_kind,artifact_sha256,status,details) VALUES (?,?,?,?,?,?)',('p',request,'strategy',hashlib.sha256(content.encode()).hexdigest(),status,json.dumps({'artifact_id':'doc'})))
    def test_no_receipt_means_no_reuse(self):
        self.assertIsNone(verified_document(self.db,'p','новое задание','strategy'))
    def test_old_request_cannot_authorize_new(self):
        self.receipt('старое задание','Старая стратегия')
        self.assertIsNone(verified_document(self.db,'p','новое задание','strategy'))
    def test_matching_receipt_authorizes(self):
        self.receipt('текущее задание','Старая стратегия')
        self.assertEqual(verified_document(self.db,'p','текущее задание','strategy'),'Старая стратегия')
    def test_rejected_receipt_cannot_authorize(self):
        self.receipt('текущее задание','Старая стратегия','rejected')
        self.assertIsNone(verified_document(self.db,'p','текущее задание','strategy'))
    def test_modified_document_cannot_authorize(self):
        self.receipt('текущее задание','Другое содержание')
        self.assertIsNone(verified_document(self.db,'p','текущее задание','strategy'))

if __name__=='__main__': unittest.main()
