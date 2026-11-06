"""Actual child process termination around the SQLite/filesystem commit boundary.

No GUI, model, or external network required. A killed child must not make a
committed task lose its receipt or remove an unrelated pending checkpoint.
"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

from dialogue_execution import ProjectConnection
from durable_json import atomic_write_json
from file_effect_outbox import drain, install, queue_document_completion


class ProcessKillRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.pid=uuid4().hex
        self.artifact=uuid4().hex
        self.task=uuid4().hex
        self.workspace=self.root/self.pid
        (self.workspace/'drafts').mkdir(parents=True)
        self.receipt=self.workspace/'drafts'/('text_receipt_task_'+self.task+'.json')
        self.checkpoint=self.workspace/'drafts'/('pending_text_task_'+self.task+'.json')
        self.request='Составь проверенный план публикаций'
        self.content='Публикация после независимого контроля'
        self.sha=hashlib.sha256(self.content.encode()).hexdigest()
        self.request_sha=hashlib.sha256(self.request.encode()).hexdigest()
        self.dbfile=self.root/'studio.sqlite3'
        with closing(sqlite3.connect(self.dbfile,factory=ProjectConnection)) as db, db:
            db.execute('CREATE TABLE manager_documents(id TEXT,project_id TEXT,content TEXT)')
            db.execute('INSERT INTO manager_documents VALUES(?,?,?)',(self.artifact,self.pid,self.content))
            install(db)
            db.commit()
        atomic_write_json(self.checkpoint,{'task_id':self.task,'request':self.request})

    def _spawn(self,code,expected):
        environ=os.environ.copy()
        environ['PYTHONPATH']=str(Path(__file__).parent)+os.pathsep+environ.get('PYTHONPATH','')
        p=subprocess.run([sys.executable,'-c',code,str(self.dbfile),str(self.workspace),
                          self.pid,self.artifact,self.request,self.content,self.task],
                         env=environ,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,timeout=15,check=False)
        self.assertEqual(p.returncode,expected,p.stderr.decode('utf-8','replace')[-2000:])

    def test_killed_after_commit_before_first_file_effect(self):
        self._spawn('''import os,sys,sqlite3,hashlib
from pathlib import Path
from dialogue_execution import ProjectConnection
from file_effect_outbox import queue_document_completion
_,dbpath,workspace,pid,artifact,request,content,task=sys.argv
root=Path(workspace)
with sqlite3.connect(dbpath,factory=ProjectConnection) as db:
 db.execute('BEGIN IMMEDIATE')
 queue_document_completion(db,project_id=pid,artifact_id=artifact,content=content,request=request,
   receipt_path=root/'drafts'/('text_receipt_task_'+task+'.json'),
   pending_path=root/'drafts'/('pending_text_task_'+task+'.json'),project_workspace=root,
   receipt_data={'request':request,'request_sha256':hashlib.sha256(request.encode()).hexdigest(),
   'artifact_id':artifact,'artifact_sha256':hashlib.sha256(content.encode()).hexdigest(),
   'saved_and_read_back':False})
 db.commit()
 os._exit(42)
''',42)
        self.assertFalse(self.receipt.exists())
        with closing(sqlite3.connect(self.dbfile,factory=ProjectConnection)) as db, db:
            self.assertEqual(drain(db,lambda _:self.workspace,export_document=lambda *_:None)['completed'],3)
        self.assertTrue(json.loads(self.receipt.read_text())['saved_and_read_back'])
        self.assertFalse(self.checkpoint.exists())

    def test_killed_after_receipt_write_before_outbox_ack(self):
        with closing(sqlite3.connect(self.dbfile,factory=ProjectConnection)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            queue_document_completion(db,project_id=self.pid,artifact_id=self.artifact,
                content=self.content,request=self.request,receipt_path=self.receipt,
                pending_path=self.checkpoint,project_workspace=self.workspace,
                receipt_data={'request':self.request,'request_sha256':self.request_sha,
                    'artifact_id':self.artifact,'artifact_sha256':self.sha,'saved_and_read_back':False})
            db.commit()
        self._spawn('''import os,sys,sqlite3
from pathlib import Path
from dialogue_execution import ProjectConnection
from file_effect_outbox import _perform
_,dbpath,workspace,*_=sys.argv
with sqlite3.connect(dbpath,factory=ProjectConnection) as db:
 db.execute("UPDATE ai_file_effect_outbox SET state='done' WHERE kind='export_document'")
 db.commit()
 row=db.execute("SELECT id,project_id,artifact_id,artifact_sha256,request_sha256,kind,receipt_name,pending_name,receipt_json FROM ai_file_effect_outbox WHERE kind='verify_receipt'").fetchone()
 _perform(db,row,Path(workspace))
 os._exit(43)
''',43)
        self.assertTrue(self.receipt.exists())
        self.assertTrue(self.checkpoint.exists())
        with closing(sqlite3.connect(self.dbfile,factory=ProjectConnection)) as db, db:
            result=drain(db,lambda _:self.workspace,export_document=lambda *_:None)
        self.assertEqual(result,{'completed':2,'failed':0,'pending':0})
        self.assertFalse(self.checkpoint.exists())

if __name__=='__main__':unittest.main()
