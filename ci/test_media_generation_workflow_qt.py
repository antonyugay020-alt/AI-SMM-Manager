"""Actual Qt -> durable intent -> native HTTP fixtures -> ledger -> owned media.

No live provider call or generated-model quality claim. Every network dependency
is explicitly replaced; images/files/threads/SQLite are real.
"""
import io
import json
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
from PySide6.QtWidgets import QApplication,QMessageBox,QInputDialog
from PIL import Image
from application_paid_media import PaidMediaComposition
from kie_media_client import KieMediaClient
from kie_media_queue_ui import KieMediaQueuePanel
from media_generation_runtime import recover_intents
from media_task_control import stopped

OWNER='a'*32
OTHER='b'*32
REMOTE='task_generation_1234567890'


class GenerationQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=sqlite3.connect(self.root/'studio.sqlite');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.executemany('INSERT INTO projects VALUES(?)',[(OWNER,),(OTHER,)])
        self.db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        self.source=self.root/OWNER/'originals'/'source.png';self.source.parent.mkdir(parents=True)
        with Image.new('RGB',(320,320),(10,20,30)) as image:image.save(self.source,'PNG')
        self.db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?)',('c'*32,OWNER,'Product',str(self.source),'fixture','now'));self.db.commit()
        self.calls=[];self.upload_entered=None;self.upload_release=None
        self.submit_entered=None;self.submit_release=None
        self.model='nano-banana-pro'
        self.current=OWNER
        self.service=PaidMediaComposition(self.db,lambda owner:self.root/owner,self.root/'ledger.sqlite')
        self.service.media_client_factory=lambda:KieMediaClient(api_key='fixture',enabled=True,opener=self.http)
        self.service.media_result_fetcher=self.download
        self.view=KieMediaQueuePanel(self.db,lambda:self.current,cost_gate=self.service.cost_gate,
            result_worker_factory=self.service.result_worker,media_service=self.service)
        self.addCleanup(self.dispose_view)
        self.view.refresh()

    def http(self,req,timeout):
        self.calls.append(req.full_url)
        if req.full_url.endswith('file-stream-upload'):
            self.assertIn(self.source.read_bytes(),b''.join(req.data))
            if self.upload_entered:
                self.upload_entered.set();self.assertTrue(self.upload_release.wait(5))
            return io.BytesIO(json.dumps({'code':200,'success':True,'data':{
                'fileSize':self.source.stat().st_size,'downloadUrl':'https://tempfile.redpandaai.co/source.png'}}).encode())
        if req.full_url.endswith('createTask'):
            body=json.loads(req.data);self.model=body['model']
            if self.submit_entered:
                self.submit_entered.set();self.assertTrue(self.submit_release.wait(5))
            return io.BytesIO(json.dumps({'code':200,'data':{'taskId':REMOTE}}).encode())
        self.assertIn('recordInfo?taskId=',req.full_url)
        return io.BytesIO(json.dumps({'code':200,'data':{'taskId':REMOTE,'model':self.model,
            'state':'success','resultJson':json.dumps({'resultUrls':['https://example.org/output.png']})}}).encode())

    def download(self,_,target):
        with Image.new('RGB',(64,64),(30,20,10)) as image:image.save(target,'PNG')

    def drain(self):
        deadline=time.monotonic()+10
        while self.view._result_worker is not None and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertIsNone(self.view._result_worker)

    def draft(self,mode='image_pro',source=False):
        self.view.generation_mode.setCurrentIndex(self.view.generation_mode.findData(mode))
        self.view.prompt.setPlainText('Product campaign')
        if source:self.view.sources.item(0).setSelected(True)
        self.view.create_draft()
        if self.view._result_worker is not None:self.drain()
        self.view.table.selectRow(0)
        return self.view.table.item(0,3).text()

    def approve_and_start(self):
        self.view.maximum_cost.setValue(.25)
        self.view.price_evidence.setText('Provider maximum quote reviewed by operator')
        self.view.price_confirmed.setChecked(True)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.view.submit_selected()

    def dispose_view(self):
        # Destroy native Qt objects deterministically on the GUI thread.
        # close() only hides a widget and leaves Python callback cycles for GC.
        from PySide6.QtCore import QCoreApplication,QEvent
        self.view.close();self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.app.processEvents()

    def test_full_actual_gui_generation_and_result_flow_uses_one_ledger(self):
        job=self.draft()
        self.assertEqual(self.calls,[])
        self.view.submit_selected()
        self.assertEqual(self.calls,[])
        self.approve_and_start();self.drain()
        self.assertEqual(self.db.execute('SELECT state FROM kie_media_tasks WHERE id=?',(job,)).fetchone()[0],'submitted')
        self.assertEqual(str(self.service.cost_gate.usage()['pending_usd']),'0.25')
        self.assertEqual(len(self.calls),1)
        self.view.table.selectRow(0);self.view.fetch_selected_result();self.drain()
        record=self.db.execute('SELECT project_id,path FROM project_files WHERE id=?',(job,)).fetchone()
        self.assertEqual(record[0],OWNER);self.assertTrue(Path(record[1]).is_file())
        self.assertEqual(len(self.calls),2)
        self.view.table.selectRow(0);self.view.fetch_selected_result();self.drain()
        self.assertEqual(len(self.calls),2)
        self.assertEqual(str(self.service.cost_gate.usage()['pending_usd']),'0.25')

    def test_gui_image_edit_prepares_sources_only_after_upload_consent(self):
        job=self.draft('image_edit',True)
        self.assertEqual(self.calls,[])
        with patch.object(QMessageBox,'question',return_value=QMessageBox.No):self.view.prepare_selected()
        self.assertEqual(self.calls,[])
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.view.prepare_selected()
        self.drain()
        self.assertEqual(len(self.calls),1)
        model,state=self.db.execute('SELECT model,state FROM kie_media_tasks WHERE id=?',(job,)).fetchone()
        self.assertEqual((model,state),('google/nano-banana-edit','prepared'))
        self.assertEqual(self.service.cost_gate.usage()['committed_usd'],0)
        self.view.table.selectRow(0);self.approve_and_start();self.drain()
        self.assertEqual(len(self.calls),2)

    def test_gui_video_reference_prepares_documented_payload(self):
        job=self.draft('video_reference',True)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.view.prepare_selected()
        self.drain()
        row=self.db.execute('SELECT payload_json FROM kie_media_tasks WHERE id=?',(job,)).fetchone()
        payload=json.loads(row[0])
        self.assertEqual(payload['model'],'kling-3.0-omni/reference-to-video')
        self.assertEqual(payload['input']['duration'],5)
        self.assertEqual(len(payload['input']['image_urls']),1)
        self.assertEqual(self.service.cost_gate.usage()['committed_usd'],0)

    def test_stop_during_upload_persists_without_paid_dispatch(self):
        job=self.draft('image_edit',True)
        self.upload_entered=threading.Event();self.upload_release=threading.Event()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.view.prepare_selected()
        self.assertTrue(self.upload_entered.wait(5))
        self.view.stop_selected();self.upload_release.set();self.drain()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.service.cost_gate.usage()['committed_usd'],0)
        self.assertEqual(self.db.execute('SELECT stop_requested FROM media_generation_intents WHERE id=?',(job,)).fetchone()[0],1)
        restarted=PaidMediaComposition(self.db,lambda owner:self.root/owner,self.root/'ledger.sqlite')
        with self.assertRaises(RuntimeError):restarted.preparation_worker(OWNER,job)
        recover_intents(self.db,OWNER)
        self.assertEqual(len(self.calls),1)

    def test_stop_during_paid_send_preserves_remote_id_and_reservation(self):
        job=self.draft()
        self.submit_entered=threading.Event();self.submit_release=threading.Event()
        self.approve_and_start()
        self.assertTrue(self.submit_entered.wait(5))
        self.view.stop_selected();self.submit_release.set();self.drain()
        state,remote=self.db.execute('SELECT state,provider_task_id FROM kie_media_tasks WHERE id=?',(job,)).fetchone()
        self.assertEqual((state,remote),('submitted',REMOTE));self.assertTrue(stopped(self.db,OWNER,job))
        self.assertEqual(str(self.service.cost_gate.usage()['pending_usd']),'0.25')
        self.view.table.selectRow(0);self.view.fetch_selected_result();self.drain()
        self.assertEqual(len(self.calls),1)
        self.view.table.selectRow(0);self.view.resume_selected()
        self.view.table.selectRow(0);self.view.fetch_selected_result();self.drain()
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],1)

    def test_restart_adopts_prepared_payload_without_second_upload(self):
        job=self.draft('image_edit',True)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.view.prepare_selected()
        self.drain();self.assertEqual(len(self.calls),1)
        # Crash window: task row committed but intent transition was lost.
        self.db.execute("UPDATE media_generation_intents SET state='preparing' WHERE id=?",(job,));self.db.commit()
        recover_intents(self.db,OWNER)
        self.view.refresh();self.view.table.selectRow(0)
        worker=self.service.preparation_worker(OWNER,job)
        self.view._launch_worker(worker);self.drain()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.db.execute('SELECT state FROM media_generation_intents WHERE id=?',(job,)).fetchone()[0],'prepared')
        self.assertEqual(self.service.cost_gate.usage()['committed_usd'],0)

    def test_changed_source_since_local_confirmation_is_never_uploaded(self):
        job=self.draft('image_edit',True)
        with Image.new('RGB',(320,320),(100,120,140)) as image:image.save(self.source,'PNG')
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.view.prepare_selected()
        self.drain()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.db.execute('SELECT state FROM media_generation_intents WHERE id=?',(job,)).fetchone()[0],'failed')

    def test_stop_at_download_boundary_prevents_publication_and_can_resume(self):
        job=self.draft();self.approve_and_start();self.drain()
        def stop_after_download(url,target):
            self.download(url,target)
            # Worker-side connection, as another process would use.
            connection=sqlite3.connect(self.root/'studio.sqlite')
            from media_task_control import request_stop
            try:request_stop(connection,OWNER,job)
            finally:connection.close()
        self.service.media_result_fetcher=stop_after_download
        self.view.table.selectRow(0);self.view.fetch_selected_result();self.drain()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)
        self.assertTrue(stopped(self.db,OWNER,job))
        self.assertFalse(list((self.root/OWNER/'.kie_downloads').glob('*.png')))
        self.service.media_result_fetcher=self.download
        self.view.table.selectRow(0);self.view.resume_selected()
        self.view.table.selectRow(0);self.view.fetch_selected_result();self.drain()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],1)
        self.assertEqual(sum(url.endswith('createTask') for url in self.calls),1)

    def test_budget_limit_blocks_gui_dispatch_without_changing_unpaid_draft(self):
        self.service.cost_gate.set_monthly_cap('.1')
        job=self.draft();self.approve_and_start();self.drain()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.service.cost_gate.usage()['committed_usd'],0)
        self.assertEqual(self.db.execute('SELECT state FROM kie_media_tasks WHERE id=?',(job,)).fetchone()[0],'prepared')

    def test_expired_quote_blocks_worker_before_reservation_or_network(self):
        job=self.draft()
        token=self.service.approve_generation(OWNER,job,'.25','verified maximum price',confirmed=True)
        self.db.execute("UPDATE media_generation_quotes SET expires_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",(job,));self.db.commit()
        with self.assertRaises(PermissionError):self.service.submission_worker(OWNER,job,token)
        self.assertEqual(self.calls,[]);self.assertEqual(self.service.cost_gate.usage()['committed_usd'],0)

    def test_local_draft_validation_stays_off_gui_thread_and_can_stop(self):
        self.view.generation_mode.setCurrentIndex(self.view.generation_mode.findData('image_edit'))
        self.view.sources.item(0).setSelected(True);self.view.prompt.setPlainText('Keep this prompt')
        entered=threading.Event();release=threading.Event();thread_ids=[]
        import media_generation_runtime as runtime
        original=runtime._digest_file
        def delayed(path):
            thread_ids.append(threading.get_ident());entered.set()
            if not release.wait(5):raise TimeoutError('fixture')
            return original(path)
        with patch.object(runtime,'_digest_file',side_effect=delayed):
            self.view.create_draft()
            self.assertTrue(entered.wait(5))
            self.assertTrue(self.view._result_worker.isRunning())
            self.view.stop_selected();release.set();self.drain()
        self.assertNotEqual(thread_ids,[threading.get_ident()])
        self.assertEqual(self.calls,[])
        self.assertEqual(self.view.prompt.toPlainText(),'Keep this prompt')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='media_generation_intents'").fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM media_generation_intents').fetchone()[0],0)

    def test_video_edit_gui_prepares_real_video_with_auto_ratio(self):
        import subprocess,shutil
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):self.skipTest('FFmpeg unavailable')
        video=self.source.parent/'input.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=720x720:r=30',
                        '-t','3','-c:v','mpeg4',str(video)],capture_output=True,check=True,timeout=20)
        self.db.execute('INSERT INTO project_files VALUES(?,?,?,?,?,?)',('d'*32,OWNER,'video','Input clip',str(video),'now'));self.db.commit()
        self.view.refresh()
        self.view.generation_mode.setCurrentIndex(self.view.generation_mode.findData('video_edit'))
        for index in range(self.view.sources.count()):
            item=self.view.sources.item(index)
            from PySide6.QtCore import Qt
            item.setSelected(item.data(Qt.UserRole)==str(video))
        self.assertEqual(self.view.aspect_ratio.currentText(),'auto')
        self.view.prompt.setPlainText('Recolor this video')
        self.view.create_draft();self.drain();self.view.table.selectRow(0)
        job=self.view.table.item(0,3).text()
        calls=[]
        def upload(req,timeout):
            calls.append(req.full_url);self.assertIn(video.read_bytes(),b''.join(req.data))
            return io.BytesIO(json.dumps({'code':200,'success':True,'data':{
                'fileSize':video.stat().st_size,'downloadUrl':'https://tempfile.redpandaai.co/source.mp4'}}).encode())
        self.service.media_client_factory=lambda:KieMediaClient(api_key='fixture',enabled=True,opener=upload)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.view.prepare_selected()
        self.drain()
        row=self.db.execute('SELECT payload_json FROM kie_media_tasks WHERE id=?',(job,)).fetchone()
        payload=json.loads(row[0])
        self.assertEqual(payload['model'],'kling-3.0-omni/transformation')
        self.assertEqual(payload['input']['aspect_ratio'],'auto')
        self.assertEqual(len(calls),1);self.assertEqual(self.service.cost_gate.usage()['committed_usd'],0)

    def uncertain_submission(self, *, mismatch=False):
        job=self.draft();payload={}
        def transport(req,timeout):
            self.calls.append(req.full_url)
            if req.full_url.endswith('createTask'):
                payload.update(json.loads(req.data))
                raise TimeoutError('provider accepted but response was lost')
            self.assertIn('recordInfo?taskId=',req.full_url)
            original=json.loads(json.dumps(payload))
            if mismatch:original['input']['prompt']='A different paid operation'
            # Provider documents input as either a nested value or JSON text.
            original['input']=json.dumps(original['input'])
            return io.BytesIO(json.dumps({'code':200,'data':{'taskId':REMOTE,
                'model':payload['model'],'state':'waiting','param':json.dumps(original)}}).encode())
        self.service.media_client_factory=lambda:KieMediaClient(api_key='fixture',enabled=True,opener=transport)
        self.approve_and_start();self.drain()
        self.assertEqual(self.db.execute('SELECT state FROM kie_media_tasks WHERE id=?',(job,)).fetchone()[0],'submission_unknown')
        return job

    def test_gui_recovers_uncertain_paid_id_by_exact_provider_log_without_rebilling(self):
        job=self.uncertain_submission()
        self.view.table.selectRow(0)
        with patch.object(QInputDialog,'getText',side_effect=[(REMOTE,True),('Provider log #123',True)]), \
             patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            self.view.recover_selected_submission()
        self.drain()
        self.assertEqual(self.db.execute('SELECT state,provider_task_id FROM kie_media_tasks WHERE id=?',(job,)).fetchone(),('submitted',REMOTE))
        self.assertEqual(str(self.service.cost_gate.usage()['pending_usd']),'0.25')
        self.assertEqual(self.db.execute('SELECT evidence FROM media_submission_recoveries WHERE job_id=?',(job,)).fetchone()[0],'Provider log #123')
        self.assertEqual(len(self.calls),2)
        self.assertEqual(sum(url.endswith('createTask') for url in self.calls),1)

    def test_provider_log_for_another_payload_cannot_attach_to_uncertain_task(self):
        job=self.uncertain_submission(mismatch=True)
        worker=self.service.recovery_worker(OWNER,job,REMOTE,'Provider log #wrong',confirmed=True)
        self.view._launch_worker(worker);self.drain()
        self.assertEqual(self.db.execute('SELECT state,provider_task_id FROM kie_media_tasks WHERE id=?',(job,)).fetchone(),('submission_unknown',None))
        self.assertEqual(sum(url.endswith('createTask') for url in self.calls),1)
        self.assertEqual(str(self.service.cost_gate.usage()['pending_usd']),'0.25')

    def test_project_switch_in_payment_confirmation_never_spends(self):
        self.draft()
        self.view.maximum_cost.setValue(.25);self.view.price_evidence.setText('verified quote');self.view.price_confirmed.setChecked(True)
        def switch(*args):self.current=OTHER;return QMessageBox.Yes
        with patch.object(QMessageBox,'question',side_effect=switch):self.view.submit_selected()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.service.cost_gate.usage()['committed_usd'],0)

if __name__=='__main__':unittest.main()
