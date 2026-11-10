"""Real QThread/SQLite/files with simulated provider; no live model acceptance."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from PIL import Image
from PySide6.QtCore import QObject,Slot
from PySide6.QtWidgets import QApplication
from kie_media_jobs import prepare
from kie_media_contracts import nano_banana_pro
from kie_media_worker import KieResultWorker
from kie_media_queue_ui import KieMediaQueuePanel
from application_paid_media import PaidMediaComposition
from durable_cost_gate import DurableCostGate

TASK='task_worker_1234567890'

class ResultCollector(QObject):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.done=[];self.failed=[]

    @Slot(str,str,str)
    def completed(self,*args):self.done.append(args)

    @Slot(str,str,str)
    def rejected(self,*args):self.failed.append(args)


class WorkerQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.path=self.root/'studio.sqlite'
        self.db=sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.executemany('INSERT INTO projects VALUES(?)',[('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',),('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',)])
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        self.db.commit()
        self.job=prepare(self.db,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',nano_banana_pro('Campaign',[]))
        self.db.execute("UPDATE kie_media_tasks SET state='submitted',provider_task_id=? WHERE id=?",(TASK,self.job))
        self.db.commit()
        self.current='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        self.query_threads=[]
        self.query=None
        self.view=KieMediaQueuePanel(self.db,lambda:self.current,result_worker_factory=self.factory)
        self.addCleanup(self.dispose_view)
        self.view.refresh()
        self.view.table.selectRow(0)

    def factory(self,owner,job):
        return KieResultWorker(self.path,self.root/owner,owner,job,
                               self.query or self.status,fetch_verified_file=self.download)

    def status(self,task):
        self.query_threads.append(threading.get_ident())
        self.assertEqual(task,TASK)
        return {'code':200,'data':{'taskId':TASK,'model':'nano-banana-pro','state':'success',
            'resultJson':json.dumps({'resultUrls':['https://example.org/output.png']})}}

    def download(self,url,target):
        with Image.new('RGB',(96,64),(10,20,30)) as image:image.save(target,'PNG')

    def drain(self):
        deadline=time.monotonic()+10
        while self.view._result_worker is not None and time.monotonic()<deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.view._result_worker)
        self.app.processEvents()

    def dispose_view(self):
        # Destroy native Qt objects deterministically on the GUI thread.
        # close() only hides a widget and leaves Python callback cycles for GC.
        from PySide6.QtCore import QCoreApplication,QEvent
        self.view.close();self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.app.processEvents()

    def test_actual_qt_refresh_publishes_to_frozen_project_and_reopens_offline(self):
        main_thread=threading.get_ident()
        self.view.fetch_selected_result()
        self.assertFalse(self.view.fetch_button.isEnabled())
        self.assertTrue(self.view._background_workers)
        self.drain()
        self.assertTrue(self.view.fetch_button.isEnabled())
        self.assertFalse(self.view._background_workers)
        self.assertNotEqual(self.query_threads,[main_thread])
        record=self.db.execute('SELECT project_id,path FROM project_files').fetchone()
        self.assertEqual(record[0],'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
        self.assertTrue(Path(record[1]).is_file())
        self.assertIn('сохранён',self.view.note.text())
        # Reopen: completed receipts validate locally, without another status call.
        count=len(self.query_threads)
        self.view.table.selectRow(0)
        self.view.fetch_selected_result()
        self.drain()
        self.assertEqual(len(self.query_threads),count)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],1)

    def test_project_switch_during_network_keeps_media_and_status_with_owner(self):
        entered=threading.Event()
        release=threading.Event()
        def query(task):
            entered.set()
            if not release.wait(5):raise TimeoutError('fixture')
            return self.status(task)
        self.query=query
        self.view.fetch_selected_result()
        self.assertTrue(entered.wait(5))
        self.current='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        self.view.refresh()
        self.assertEqual(self.view.table.rowCount(),0)
        note=self.view.note.text()
        release.set()
        self.drain()
        self.assertEqual(self.view.note.text(),note)
        self.assertEqual(self.db.execute('SELECT project_id FROM project_files').fetchone()[0],'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')

    def test_provider_failure_is_safe_and_retains_recoverable_task(self):
        def fail(_):raise TimeoutError('secret signed URL credential')
        self.query=fail
        self.view.fetch_selected_result()
        self.drain()
        self.assertNotIn('secret',self.view.note.text())
        self.assertEqual(self.db.execute('SELECT state FROM kie_media_tasks').fetchone()[0],'submitted')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)

    def test_stale_selection_never_starts_worker(self):
        self.current='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        with patch.object(self.view,'_result_worker_factory') as factory:
            self.view.fetch_selected_result()
        factory.assert_not_called()

    def test_composition_constructs_native_worker_without_network_at_boot(self):
        composition=PaidMediaComposition(self.db,lambda pid:self.root/pid,self.root/'ledger.sqlite')
        with patch.dict(os.environ,{'KIE_AI_API_KEY':'fixture-key'}):
            with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network at boot')):
                worker=composition.result_worker('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',self.job)
                self.assertFalse(worker.isRunning())
                self.assertEqual(worker.project_id,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
                self.assertEqual(worker.workspace_root,self.root/'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
        with self.assertRaises(ValueError):composition.result_worker('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',self.job)

    def test_real_video_result_is_verified_and_published_by_native_qthread(self):
        import shutil
        import subprocess
        import hashlib
        from kie_media_contracts import kling_omni_reference_to_video
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('native FFmpeg/ffprobe unavailable')
        source=self.root/'provider-fixture.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
            'color=c=blue:s=320x320:r=30','-t','1','-c:v','mpeg4',str(source)],
            check=True,capture_output=True,timeout=20)
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        payload=kling_omni_reference_to_video('Animate',['https://example.org/photo.png'])
        video_job=prepare(self.db,'a'*32,payload)
        video_id='task_video_1234567890'
        self.db.execute("UPDATE kie_media_tasks SET state='submitted',provider_task_id=? WHERE id=?",
                        (video_id,video_job))
        self.db.commit()
        def status(_):
            return {'code':200,'data':{'taskId':video_id,'model':payload['model'],'state':'success',
                'resultJson':json.dumps({'resultUrls':['https://example.org/result.mp4']})}}
        worker=KieResultWorker(self.path,self.root/('a'*32),'a'*32,video_job,status,
                              fetch_verified_file=lambda _,target:shutil.copyfile(source,target))
        collector=ResultCollector(self.view)
        done=collector.done;failed=collector.failed
        worker.setParent(self.view)
        worker.done.connect(collector.completed)
        worker.failed.connect(collector.rejected)
        worker.start()
        deadline=time.monotonic()+10
        while worker.isRunning() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertTrue(worker.wait(1000))
        self.app.processEvents()
        self.assertEqual(failed,[])
        self.assertEqual(len(done),1)
        target=Path(done[0][2])
        self.assertEqual(target.suffix,'.mp4')
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),before)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
        self.assertEqual(self.db.execute('SELECT kind FROM project_files WHERE id=?',
                                        (video_job,)).fetchone()[0],'video')

    def test_two_native_workers_reconcile_single_publication(self):
        barrier=threading.Barrier(2)
        def query(task):
            barrier.wait(timeout=5)
            return self.status(task)
        workers=[KieResultWorker(self.path,self.root/('a'*32),'a'*32,self.job,
                                query,fetch_verified_file=self.download) for _ in range(2)]
        collector=ResultCollector(self.view)
        done=collector.done;failed=collector.failed
        for worker in workers:
            worker.setParent(self.view)
            worker.done.connect(collector.completed)
            worker.failed.connect(collector.rejected)
            worker.start()
        deadline=time.monotonic()+10
        while any(worker.isRunning() for worker in workers) and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        for worker in workers:self.assertTrue(worker.wait(1000))
        self.app.processEvents()
        self.assertEqual(failed,[])
        self.assertEqual(len(done),2)
        self.assertEqual(done[0][2],done[1][2])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT state FROM kie_media_tasks').fetchone()[0],'completed')

    def test_ledger_connections_close_on_success_and_failure(self):
        connections=[]
        original=DurableCostGate._connect
        def connect(gate):
            db=original(gate)
            connections.append(db)
            return db
        with patch.object(DurableCostGate,'_connect',connect):
            gate=DurableCostGate(self.root/'ledger.sqlite')
            gate.usage()
            with self.assertRaises(PermissionError):gate.reserve('kie.ai','task','1')
        for db in connections:
            with self.assertRaises(sqlite3.ProgrammingError):db.execute('SELECT 1')

if __name__=='__main__':unittest.main()
