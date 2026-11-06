"""Regression: segmentation may never apply a late mask to another project/photo."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from project_operation_guard import mask_owner_matches, project_operation_busy


class Worker:
    def __init__(self, running=True, failed=False):
        self.running = running
        self.failed = failed
    def isRunning(self):
        if self.failed:
            raise RuntimeError('Qt object deleted')
        return self.running


class TestMaskOwnership(unittest.TestCase):
    def test_launch_owner_matches(self):
        with TemporaryDirectory() as tmp:
            img=Path(tmp)/'image.png'
            img.touch()
            owner=('project-a', str(img.resolve()))
            self.assertTrue(mask_owner_matches(owner,'project-a',img))

    def test_different_project_denied(self):
        with TemporaryDirectory() as tmp:
            img=Path(tmp)/'image.png'
            img.touch()
            self.assertFalse(mask_owner_matches(('project-a',str(img.resolve())),'project-b',img))

    def test_same_project_different_source_denied(self):
        with TemporaryDirectory() as tmp:
            src=Path(tmp)/'a.png'; src.touch()
            other=Path(tmp)/'b.png'; other.touch()
            self.assertFalse(mask_owner_matches(('p',str(src.resolve())),'p',other))

    def test_no_owner_denied(self):
        self.assertFalse(mask_owner_matches(None,'p','file.png'))
        self.assertFalse(mask_owner_matches(('p','x'),None,'x'))

    def test_mask_workers_block_project_switch_and_message(self):
        for attr in ('neural_worker','smart_worker','neural_install','cuda_install_worker',
                     'vision_worker','smart_mask_worker'):
            with self.subTest(attr=attr):
                self.assertTrue(project_operation_busy(SimpleNamespace(**{attr:Worker()})))

    def test_completed_worker_does_not_lock_gui_permanently(self):
        for attr in ('neural_worker','smart_worker','neural_install','cuda_install_worker',
                     'vision_worker','smart_mask_worker'):
            with self.subTest(attr=attr):
                self.assertFalse(project_operation_busy(SimpleNamespace(**{attr:Worker(False)})))

    def test_deleted_qt_object_fails_closed(self):
        self.assertTrue(project_operation_busy(SimpleNamespace(smart_worker=Worker(failed=True))))

    def test_active_text_executor_still_blocks(self):
        self.assertTrue(project_operation_busy(SimpleNamespace(task_worker=Worker(False))))

    def test_app_handlers_are_wired_to_launch_owner_and_guard(self):
        # Check production code structurally without importing Qt on headless CI.
        tree=ast.parse((Path(__file__).parent/'app.py').read_text(encoding='utf-8'))
        studio=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Studio')
        methods={n.name:n for n in studio.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
        for name in ('neural_mask','smart_mask','smart_mask_done','_mask_install_done','_mask_install_finished','closeEvent'):
            self.assertIn(name,methods)
        def called(name):
            return {n.func.id if isinstance(n.func,ast.Name) else n.func.attr
                    for n in ast.walk(methods[name]) if isinstance(n,ast.Call)
                    if isinstance(n.func,(ast.Name,ast.Attribute))}
        self.assertIn('mask_owner_matches',called('smart_mask_done'))
        self.assertIn('mask_owner_matches',called('_mask_install_finished'))
        self.assertNotIn('neural_mask',called('_mask_install_done'))
        self.assertIn('neural_mask',called('_mask_install_finished'))
        self.assertIn('finished', (Path(__file__).parent/'app.py').read_text(encoding='utf-8').split('self.neural_install=InstallSegmentationWorker()',1)[1].split('self.neural_install.start()',1)[0])
        self.assertIn('project_operation_busy',called('closeEvent'))
        self.assertIn('_mask_execution_busy',called('neural_mask'))
        self.assertIn('_mask_execution_busy',called('smart_mask'))


if __name__=='__main__': unittest.main()


class MaskInstallerConcurrencyRegression(unittest.TestCase):
    def test_running_installation_blocks_duplicate_mask_request(self):
        import ast
        from pathlib import Path
        source = Path(__file__).with_name('app.py').read_text(encoding='utf-8')
        studio = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == 'Studio')
        busy = next(node for node in studio.body if isinstance(node, ast.FunctionDef) and node.name == '_mask_execution_busy')
        code = compile(ast.Module(body=[busy], type_ignores=[]), '<mask-guard>', 'exec')
        namespace = {}
        exec(code, namespace)
        class Running:
            def isRunning(self): return True
        class Idle:
            def isRunning(self): return False
        from types import SimpleNamespace
        self.assertTrue(namespace['_mask_execution_busy'](SimpleNamespace(neural_worker=None,smart_worker=None,neural_install=Running())))
        self.assertFalse(namespace['_mask_execution_busy'](SimpleNamespace(neural_worker=None,smart_worker=None,neural_install=Idle())))
