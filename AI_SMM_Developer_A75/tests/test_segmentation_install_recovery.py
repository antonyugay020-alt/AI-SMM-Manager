"""Ensure segmentation installation can fail closed instead of looping."""
import ast
import unittest
from pathlib import Path

class SegmentationInstallRecoveryTests(unittest.TestCase):
    def test_successful_installer_requires_importable_runtime(self):
        src = (Path(__file__).parent / 'app.py').read_text(encoding='utf-8')
        studio = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == 'Studio')
        handler = next(n for n in studio.body if isinstance(n, ast.FunctionDef) and n.name == '_mask_install_finished')
        code = ast.unparse(handler)
        self.assertIn('importlib.invalidate_caches()', code)
        self.assertIn('import rembg', code)
        self.assertIn('import onnxruntime', code)
        self.assertIn('except (ImportError, OSError)', code)
        self.assertLess(code.index('import onnxruntime'), code.index('self.neural_mask()'))

if __name__ == '__main__': unittest.main()
