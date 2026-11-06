"""A shipped application must not depend on its test directory for production code."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'tests'

class CanonicalSourceGraphTests(unittest.TestCase):
    def test_every_test_tree_runtime_module_is_in_application_root(self):
        for module in TESTS.glob('*.py'):
            if module.name.startswith('test_') or module.name in ('conftest.py',):
                continue
            deployed=ROOT / module.name
            self.assertTrue(deployed.is_file(),module.name)
            self.assertEqual(module.read_bytes(),deployed.read_bytes(),
                             f'Divergent runtime implementation: {module.name}')

    def test_application_import_graph_has_no_missing_local_modules(self):
        root_modules={p.stem for p in ROOT.glob('*.py')}
        test_modules={p.stem for p in TESTS.glob('*.py')}
        for runtime in (p for p in ROOT.glob('*.py') if not p.name.startswith('test_')):
            tree=ast.parse(runtime.read_text(encoding='utf-8'),str(runtime))
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):
                    names=[alias.name.split('.')[0] for alias in node.names]
                elif isinstance(node,ast.ImportFrom) and node.module:
                    names=[node.module.split('.')[0]]
                else: continue
                for name in names:
                    if name in test_modules:
                        self.assertIn(name,root_modules,(runtime.name,name))

    def test_gui_uses_registered_root_not_legacy_lookup(self):
        tree=ast.parse((ROOT/'app.py').read_text(encoding='utf-8'))
        studio=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Studio')
        init=next(n for n in studio.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
        self.assertIn('ProjectWorkspaceResolver',ast.unparse(init))
        self.assertNotIn('ApplicationRuntime(self.db, project_dir)',ast.unparse(init))

if __name__=='__main__':unittest.main()
