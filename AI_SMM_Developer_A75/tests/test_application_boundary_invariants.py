"""Static service-graph invariants protecting Qt-free transaction ownership.

These are architecture checks, distinct from the real SQLite integration tests.
"""
import ast
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent

class ApplicationBoundaryInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse((ROOT/'app.py').read_text(encoding='utf-8'))
        cls.studio=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='Studio')

    def function(self,name):
        return next(node for node in self.studio.body if isinstance(node,ast.FunctionDef) and node.name==name)

    def test_document_and_outcome_handlers_never_own_commits(self):
        for name in ('save_ai_document','set_pending_task','_apply_task_result_body','task_failed'):
            with self.subTest(handler=name):
                node=self.function(name)
                for call in (n for n in ast.walk(node) if isinstance(n,ast.Call)):
                    method=call.func
                    if (isinstance(method,ast.Attribute) and isinstance(method.value,ast.Attribute)
                            and isinstance(method.value.value,ast.Name)
                            and method.value.value.id=='self' and method.value.attr=='db'):
                        self.assertNotIn(method.attr,('commit','rollback'),
                            f'{name} must never commit or roll back another service operation')

    def test_project_state_mutation_is_authorized_by_core(self):
        for name,service_method in (
            ('save_ai_document','stage_generated_document'),
            ('set_pending_task','park_in_transaction'),
            ('task_failed','record_execution_failure')):
            node=self.function(name)
            calls=[n.func.attr for n in ast.walk(node)
                   if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
            self.assertIn(service_method,calls)

    def test_production_and_test_mirrors_are_identical(self):
        for production in ROOT.glob('*.py'):
            twin=ROOT/'tests'/production.name
            if twin.exists():
                with self.subTest(module=production.name):
                    self.assertEqual(production.read_bytes(),twin.read_bytes())

if __name__=='__main__':unittest.main()
