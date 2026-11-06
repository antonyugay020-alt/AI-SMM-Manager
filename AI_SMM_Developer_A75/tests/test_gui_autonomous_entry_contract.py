"""The Windows worker must enter the same qualified review pipeline as CLI acceptance.

No Qt dependency is required to guard the production call boundary. The injected
router / author / reviewer check below exercises the actual text workflow.
"""
import ast
import unittest
from pathlib import Path
from unittest.mock import patch
from text_workflow import run_text_task

ROOT = Path(__file__).resolve().parent


class QtAutonomousEntryContractTests(unittest.TestCase):
    def test_qt_worker_does_not_inject_legacy_planner(self):
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
        workers = [node for node in tree.body if isinstance(node,ast.ClassDef) and node.name == 'TaskWorker']
        self.assertEqual(len(workers),1)
        calls = [node for node in ast.walk(workers[0]) if isinstance(node,ast.Call)
                 and isinstance(node.func,ast.Name) and node.func.id == 'run_text_task']
        self.assertEqual(len(calls),1)
        self.assertNotIn('planner',[kw.arg for kw in calls[0].keywords])
        self.assertNotIn('quality_cycle',[kw.arg for kw in calls[0].keywords])

    def test_production_contract_qualifies_before_router_author_and_review(self):
        events=[]
        def eligible(*args,**kwargs):
            events.append('qualification')
            return ({'requirements':'reviewer:1'}, ['reviewer:1'])
        def route(*args,**kwargs):
            events.append('routing')
            return {'action':'write','answer':'','content_contract':None}
        def author(*args,**kwargs):
            events.append('author')
            return {'action':'write','answer':'Готовый черновик', 'content_contract':None}
        def review(*args,**kwargs):
            events.append('independent_review')
            return {'action':'write','answer':'Готовый черновик',
                    'quality_review':{'status':'PASS','pass':True}}
        with patch('generic_text_review.select_reviewer_models',side_effect=eligible), \
             patch('agent_runtime.route_text_intent',side_effect=route), \
             patch('agent_runtime.write_requested_artifact',side_effect=author), \
             patch('generic_text_review.run_quality_cycle',side_effect=review):
            result=run_text_task('Напиши пост',{},model='author:1')
        self.assertEqual(events,['qualification','routing','author','independent_review'])
        self.assertEqual(result['answer'],'Готовый черновик')

    def test_qualification_failure_cannot_reach_author(self):
        with patch('generic_text_review.select_reviewer_models',side_effect=ValueError('no independent reviewers')), \
             patch('agent_runtime.route_text_intent',side_effect=AssertionError('router should not run')), \
             patch('agent_runtime.write_requested_artifact',side_effect=AssertionError('author should not run')):
            with self.assertRaisesRegex(ValueError,'no independent reviewers'):
                run_text_task('Напиши пост',{},model='author:1')

if __name__ == '__main__':
    unittest.main()
