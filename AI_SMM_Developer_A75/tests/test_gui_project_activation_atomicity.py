"""Project switch must not abandon current UI state if destination is not ready."""
import sqlite3
import sys
import types
import unittest
from types import SimpleNamespace
from test_cross_operation_completion_safety import get_method


class AtomicProjectActivationTests(unittest.TestCase):
    def setUp(self):
        self.messages=[]
        self.change=[]
        self.clear=[]
        self.data=SimpleNamespace(workspace=lambda pid: None)
        self.projects=SimpleNamespace(activate=lambda pid: None)
        self.retirements=SimpleNamespace(recover=lambda **kw:SimpleNamespace(failed=()))
        def prepare_project_switch(pid):
            self.projects.activate(pid)
            result=self.retirements.recover(project_id=pid)
            if result.failed:
                raise RuntimeError('Destination project has unresolved result retirements')
            return self.data.workspace(pid)
        self.ui=SimpleNamespace(
            current_project='original',project_ids=['original','destination'],
            _switching_project=False,_pending_project_delete={'scope':'project'},
            _semantic_media_project=None,core=SimpleNamespace(
                projects=self.projects,data=self.data,result_retirements=self.retirements,
                prepare_project_switch=prepare_project_switch),
            save_document_if_changed=lambda:True,
            projects=SimpleNamespace(currentIndex=lambda:1,blockSignals=lambda value:False,
                setCurrentIndex=lambda value:self.change.append(value)),
            statusBar=lambda:SimpleNamespace(showMessage=lambda *a:None),
            assets=SimpleNamespace(clear=lambda:self.clear.append('assets')),
        )
        self.messagebox=SimpleNamespace(warning=lambda *args:self.messages.append(args))

    def _invoke(self):
        boundary=types.ModuleType('project_operation_guard')
        boundary.project_operation_busy=lambda ui:False
        old=sys.modules.get('project_operation_guard')
        sys.modules['project_operation_guard']=boundary
        try:
            method=get_method('select_project',{'sqlite3':sqlite3,'QMessageBox':self.messagebox})
            method(self.ui,1)
        finally:
            if old is None:sys.modules.pop('project_operation_guard',None)
            else:sys.modules['project_operation_guard']=old

    def _assert_not_switched(self):
        self.assertEqual(self.ui.current_project,'original')
        self.assertEqual(self.ui._pending_project_delete,{'scope':'project'})
        self.assertFalse(self.ui._switching_project)
        self.assertEqual(self.clear,[])
        self.assertEqual(self.change,[0])
        self.assertEqual(len(self.messages),1)

    def test_failure_loading_target_read_model_preserves_current_workspace(self):
        def fail(pid):
            self.assertEqual(pid,'destination')
            raise sqlite3.OperationalError('unavailable')
        self.data.workspace=fail
        self._invoke()
        self._assert_not_switched()

    def test_crash_replay_failure_preserves_current_workspace(self):
        self.retirements.recover=lambda **kw:SimpleNamespace(failed=(('record','hash mismatch'),))
        self._invoke()
        self._assert_not_switched()

    def test_project_activation_failure_preserves_current_workspace(self):
        self.projects.activate=lambda pid: (_ for _ in ()).throw(ValueError('invalid project'))
        self._invoke()
        self._assert_not_switched()


if __name__=='__main__':unittest.main()
