import ast
import pathlib
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parent
class TestPreflightDiagnostics(unittest.TestCase):
    def test_skip_reason_reports_blocking_checks(self):
        source=(ROOT/'windows_acceptance.py').read_text(encoding='utf-8')
        tree=ast.parse(source)
        calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and
               isinstance(node.func,ast.Name) and node.func.id=='record' and
               node.args and isinstance(node.args[0],ast.Constant) and node.args[0].value=='live_gui_full_workflow']
        branches=[call for call in calls if any(k.arg=='blocking_checks' for k in call.keywords)]
        self.assertEqual(len(branches),1)
        branch=branches[0]
        self.assertNotIn('Ollama not ready',ast.unparse(branch))
        self.assertIn('reviewer_models',ast.unparse(branch))
        self.assertIn('openai_api_preflight',ast.unparse(branch))
