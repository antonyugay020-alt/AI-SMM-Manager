"""Actual Qt local-media queue without a paid ledger or provider credentials."""
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication
from PIL import Image
from local_media_dispatcher import LocalMediaDispatcher
from kie_media_queue_ui import KieMediaQueuePanel
import local_media_jobs as jobs

OWNER='a'*32
OTHER='b'*32


class LocalQueueQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'Проект Ё — 你好';self.root.mkdir()
        self.db=sqlite3.connect(self.root/'studio.sqlite');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.executemany('INSERT INTO projects VALUES(?)',[(OWNER,),(OTHER,)])
        self.db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        source=self.root/OWNER/'originals'/'Съёмка Ё.png';source.parent.mkdir(parents=True)
        with Image.new('RGB',(160,120),(30,50,70)) as image:image.save(source,'PNG')
        self.db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?)',('source',OWNER,'Source image',str(source),jobs.digest_file(source),'now'));self.db.commit()
        self.current=OWNER
        self.service=LocalMediaDispatcher(self.db,lambda owner:self.root/owner)
        self.view=KieMediaQueuePanel(self.db,lambda:self.current,local_service=self.service)
        self.addCleanup(self.dispose_view)
        self.view.refresh();self.view.sources.item(0).setSelected(True)
        self.view.smm_task.setCurrentText('image_resize');self.view.local_width.setValue(128);self.view.local_height.setValue(96)

    def dispose_view(self):
        self.view.close();self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents()

    def drain(self):
        deadline=time.monotonic()+10
        while self.view._result_worker is not None and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertIsNone(self.view._result_worker)

    def test_actual_gui_local_task_publishes_without_paid_journal(self):
        self.assertFalse(self.view.monthly_limit.isEnabled());self.assertTrue(self.view.local_button.isEnabled())
        self.view.execute_local();self.drain()
        self.assertEqual(self.view.table.rowCount(),1)
        self.assertEqual(self.view.table.item(0,1).text(),'local/pillow')
        self.assertEqual(self.view.table.item(0,2).text(),'completed')
        path=self.db.execute('SELECT path FROM project_files').fetchone()[0]
        with Image.open(path) as image:self.assertEqual(image.size,(128,96))
        self.assertFalse((self.root/'paid_cost_ledger.sqlite').exists())
        self.assertFalse(self.view._background_workers)

    def test_project_switch_during_native_worker_never_retargets_output(self):
        entered=threading.Event();release=threading.Event();original=jobs._render
        def delayed(request,sources,target,check):
            entered.set()
            if not release.wait(5):raise TimeoutError('fixture')
            return original(request,sources,target,check)
        with patch.object(jobs,'_render',side_effect=delayed):
            self.view.execute_local();self.assertTrue(entered.wait(5))
            self.current=OTHER;self.view.refresh();note=self.view.note.text()
            release.set();self.drain()
        self.assertEqual(self.view.table.rowCount(),0);self.assertEqual(self.view.note.text(),note)
        self.assertEqual(self.db.execute('SELECT project_id FROM project_files').fetchone()[0],OWNER)

    def test_local_stop_is_durable_and_prevents_publication(self):
        entered=threading.Event();release=threading.Event()
        def delayed(request,sources,target,check):
            entered.set()
            if not release.wait(5):raise TimeoutError('fixture')
            check()
        with patch.object(jobs,'_render',side_effect=delayed):
            self.view.execute_local();self.assertTrue(entered.wait(5))
            self.view.stop_selected();release.set();self.drain()
        self.assertEqual(self.db.execute('SELECT state,stop_requested FROM local_media_jobs').fetchone(),('cancelled',1))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)
        restarted=LocalMediaDispatcher(self.db,lambda owner:self.root/owner)
        restarted.recover(OWNER)
        self.assertEqual(restarted.tasks(OWNER)[0]['state'],'cancelled')

    def test_recovery_does_not_interrupt_a_live_native_worker(self):
        entered=threading.Event();release=threading.Event();original=jobs._render
        def delayed(request,sources,target,check):
            entered.set()
            if not release.wait(5):raise TimeoutError('fixture')
            check();return original(request,sources,target,check)
        with patch.object(jobs,'_render',side_effect=delayed):
            self.view.execute_local();self.assertTrue(entered.wait(5))
            self.service.recover(OWNER)
            self.assertEqual(self.db.execute('SELECT state FROM local_media_jobs').fetchone(),('running',))
            release.set();self.drain()
        job,state=self.db.execute('SELECT id,state FROM local_media_jobs').fetchone()
        self.assertEqual(state,'completed')
        self.assertEqual(self.db.execute('SELECT id,state FROM local_media_jobs').fetchall(),[(job,'completed')])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files WHERE id=?',(job,)).fetchone()[0],1)

    def test_orphan_recovery_and_explicit_retry_keep_the_same_task_identity(self):
        with patch.object(jobs,'_render',side_effect=RuntimeError('fixture failure')):
            self.view.execute_local();self.drain()
        job=self.db.execute('SELECT id FROM local_media_jobs').fetchone()[0]
        self.db.execute("UPDATE local_media_jobs SET state='running' WHERE id=?",(job,));self.db.commit()
        self.service.recover(OWNER);self.view.refresh()
        self.assertEqual(self.db.execute('SELECT state FROM local_media_jobs').fetchone(),('interrupted',))
        self.view.table.selectRow(0);self.view.execute_local();self.drain()
        self.assertEqual(self.db.execute('SELECT id,state FROM local_media_jobs').fetchall(),[(job,'completed')])

    def test_stop_inside_ingest_preflight_prevents_database_publication(self):
        original=jobs.ingest
        def stop_before_commit(db,workspace,source,**options):
            connection=sqlite3.connect(self.root/'studio.sqlite')
            try:jobs.stop_job(connection,options['project_id'],options['item_id'])
            finally:connection.close()
            return original(db,workspace,source,**options)
        with patch.object(jobs,'ingest',side_effect=stop_before_commit):self.view.execute_local();self.drain()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT state FROM local_media_jobs').fetchone()[0],'cancelled')

if __name__=='__main__':unittest.main()
