"""Actual GUI/worker approval bridge with mocked dialogs and provider HTTP."""
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from PySide6.QtCore import QThread,Signal
from PySide6.QtWidgets import QApplication,QWidget,QInputDialog,QMessageBox
from application_paid_media import PaidMediaComposition
from qt_text_approval import TextApprovalBroker
from paid_text_runtime import text_authorization_scope
from text_providers import call_text


class TextWorker(QThread):
    done=Signal(object)
    failed=Signal(str)
    def run(self):
        try:
            with text_authorization_scope(self.text_authorizer):
                result=call_text('openai/fixture',[{'role':'user','content':'Project request'}],
                                 environ={'OPENAI_API_KEY':'fixture-key'})
            self.done.emit(result)
        except Exception as exc:self.failed.emit(type(exc).__name__)


class ApprovalBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.db=sqlite3.connect(root/'studio.sqlite');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute("INSERT INTO projects VALUES('owner')");self.db.commit()
        self.service=PaidMediaComposition(self.db,lambda owner:root/owner,root/'ledger.sqlite')
        self.window=QWidget();self.addCleanup(self.dispose_window)
        self.window.current_project='owner'
        self.worker=TextWorker();self.window.task_worker=self.worker
        self.broker=TextApprovalBroker(self.window,self.service)
        self.worker.text_authorizer=self.broker.for_worker(self.worker,'owner','task')
        self.done=[];self.failed=[]
        self.worker.done.connect(self.done.append);self.worker.failed.connect(self.failed.append)

    def drain(self):
        deadline=time.monotonic()+10
        while self.worker.isRunning() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertTrue(self.worker.wait(1000));self.app.processEvents()

    def dispose_window(self):
        from PySide6.QtCore import QCoreApplication,QEvent
        self.window.close();self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.app.processEvents()

    def test_worker_waits_for_exact_gui_approval_and_uses_shared_ledger(self):
        thread_ids=[]
        def network(*args,**kwargs):thread_ids.append(threading.get_ident());return 'Accepted',{}
        with patch.object(QInputDialog,'getText',side_effect=[('.25',True),('verified provider maximum',True)]) as dialog, \
             patch.object(QMessageBox,'question',return_value=QMessageBox.Yes), \
             patch('text_providers._chat_request',side_effect=network) as http:
            self.worker.start();self.drain()
        self.assertEqual(self.failed,[]);self.assertEqual(len(self.done),1)
        self.assertEqual(dialog.call_count,2);http.assert_called_once()
        self.assertNotEqual(thread_ids,[threading.get_ident()])
        self.assertEqual(str(self.service.cost_gate.usage()['pending_usd']),'0.25')
        db=sqlite3.connect(self.service.ledger_path)
        try:self.assertEqual(db.execute("SELECT COUNT(*) FROM cost_audit WHERE event='authorize'").fetchone()[0],1)
        finally:db.close()

    def test_declined_confirmation_never_reserves_or_dispatches(self):
        with patch.object(QInputDialog,'getText',side_effect=[('.25',True),('verified maximum',True)]), \
             patch.object(QMessageBox,'question',return_value=QMessageBox.No), \
             patch('text_providers._chat_request') as http:
            self.worker.start();self.drain()
        http.assert_not_called();self.assertEqual(self.failed,['PaymentDenied'])
        self.assertEqual(self.service.cost_gate.usage()['committed_usd'],0)

    def test_project_switch_during_dialog_blocks_the_stale_authorization(self):
        def switch(*args,**kwargs):self.window.current_project='other';return '.25',True
        with patch.object(QInputDialog,'getText',side_effect=switch),patch('text_providers._chat_request') as http:
            self.worker.start();self.drain()
        http.assert_not_called();self.assertEqual(self.service.cost_gate.usage()['committed_usd'],0)

    def test_interrupted_worker_does_not_leave_stale_approval_prompt(self):
        with patch.object(QInputDialog,'getText') as dialog,patch('text_providers._chat_request') as http:
            self.worker.start();self.worker.requestInterruption();self.drain()
        dialog.assert_not_called();http.assert_not_called()
        self.assertEqual(self.service.cost_gate.usage()['committed_usd'],0)

if __name__=='__main__':unittest.main()
