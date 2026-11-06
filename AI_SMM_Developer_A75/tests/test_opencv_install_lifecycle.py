"""Regression: OpenCV installation must not block Qt GUI or cross project ownership."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class OpenCVInstallLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
        cls.studio = next(n for n in cls.tree.body if isinstance(n, ast.ClassDef) and n.name == 'Studio')

    def test_install_is_background_thread(self):
        worker = next(n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == 'InstallOpenCVWorker')
        self.assertEqual(ast.unparse(worker.bases[0]), 'QThread')
        smart = next(n for n in self.studio.body if isinstance(n, ast.FunctionDef) and n.name == 'smart_mask')
        self.assertNotIn('subprocess.run(', ast.unparse(smart))
        self.assertIn('InstallOpenCVWorker()', ast.unparse(smart))

    def test_continuation_is_finished_and_owner_checked(self):
        smart = next(n for n in self.studio.body if isinstance(n, ast.FunctionDef) and n.name == 'smart_mask')
        self.assertIn('opencv_install.finished.connect(self._opencv_install_finished)', ast.unparse(smart))
        finish = next(n for n in self.studio.body if isinstance(n, ast.FunctionDef) and n.name == '_opencv_install_finished')
        source = ast.unparse(finish)
        self.assertIn('mask_owner_matches(owner, self.current_project, self.source.path)', source)
        self.assertIn('if not succeeded:', source)
        self.assertIn('self.smart_mask(mode)', source)

    def test_worker_in_lifecycle_and_project_guard(self):
        for filename in ('project_operation_guard.py','qt_worker_lifecycle.py'):
            self.assertIn("'opencv_install'", (ROOT / filename).read_text(encoding='utf-8'))

if __name__ == '__main__':
    unittest.main()
