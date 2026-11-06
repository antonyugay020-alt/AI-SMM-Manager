import unittest
from pathlib import Path
from unittest.mock import patch

from acceptance_policy import attempt_limit, is_first_pass_acceptance
import compound_tasks
import generic_text_review
import plan_governance
import production_pipeline
from semantic_brief import plan_slots


class AcceptancePolicyTests(unittest.TestCase):
    def test_only_explicit_randomized_run_uses_one_generation_attempt(self):
        self.assertTrue(is_first_pass_acceptance({'randomized_acceptance_run':True}))
        self.assertFalse(is_first_pass_acceptance({'randomized_acceptance_run':'true'}))
        self.assertFalse(is_first_pass_acceptance({}))
        self.assertEqual(attempt_limit({'randomized_acceptance_run':True},7),1)
        self.assertEqual(attempt_limit({'randomized_acceptance_run':True},3),1)
        self.assertEqual(attempt_limit({},7),7)

    def test_invalid_production_retry_budget_is_rejected(self):
        for value in (0,-1,1.5,True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                attempt_limit({},value)

    def test_first_pass_policy_is_wired_to_all_expensive_generation_loops(self):
        compound=Path(compound_tasks.__file__).read_text(encoding='utf-8-sig')
        standalone=Path(generic_text_review.__file__).read_text(encoding='utf-8-sig')
        planner=Path(plan_slots.__code__.co_filename).read_text(encoding='utf-8-sig')
        governance=Path(plan_governance.__file__).read_text(encoding='utf-8-sig')
        pipeline=Path(production_pipeline.__file__).read_text(encoding='utf-8-sig')
        self.assertIn('attempts=attempt_limit(context,3)',compound)
        self.assertIn('range(attempt_limit(context,2))',compound)
        self.assertIn('range(attempt_limit(context,7))',compound)
        self.assertIn('if initial_generation_only(context) and pending:',compound)
        self.assertIn('if first_pass_acceptance:\n                                    remember_rejection(ident,candidate,critical',compound)
        import ast
        conditions=[ast.unparse(node.test) for node in ast.walk(ast.parse(compound)) if isinstance(node,ast.If)]
        self.assertTrue(any(all(part in condition for part in ('not first_pass_acceptance','slot in pending',
            'ident in replan_reasons','ident not in unavailable_publications')) for condition in conditions))
        self.assertIn('if (first_pass_acceptance and slot in pending and ident in replan_reasons):',compound)
        self.assertIn("planner_args['first_pass_only'] = True",compound)
        self.assertIn('first_pass_only=False',planner)
        self.assertLess(planner.index('if first_pass_only and len(week_slots)<per_week:'),
                        planner.index('while len(week_slots)<per_week:'))
        self.assertIn('if not allow_replan:',governance)
        self.assertIn("governance_args['allow_replan']=False",compound)
        self.assertIn('if is_first_pass_acceptance(context) and final_repair_limit(context,1)==0:',standalone)
        self.assertIn("state=dict(context)",pipeline)
        self.assertIn("retry_budget=1 if is_first_pass_acceptance(state) else 2",pipeline)
        self.assertIn("repair_round_limit=final_repair_limit(state,2)",pipeline)

    def test_topic_planning_first_pass_fails_without_retrying_a_bad_reply(self):
        calls=[]
        def ask(*args,**kwargs):
            calls.append(1)
            return '{"posts":[]}'
        with self.assertRaisesRegex(ValueError,'первый ответ'):
            plan_slots(ask,'model','request','strategy',
                {'channels':'Instagram','channels_basis':'explicit','audience':'участники'},
                1,per_week=1,first_pass_only=True)
        self.assertEqual(len(calls),1)
        with self.assertRaisesRegex(TypeError,'булевым'):
            plan_slots(ask,'model','request','strategy',
                {'channels':'Instagram','channels_basis':'explicit','audience':'участники'},
                1,per_week=1,first_pass_only='true')

    def test_first_pass_plan_review_does_not_generate_replacement_topics(self):
        slots=[{'week':1,'day':'Вт','platform':'Instagram','format':'post',
                'topic':'Тема вводной публикации','brief':'Раскрыть конкретный вопрос',
                'goal':'Поддержать запрос','audience':'Подписчики'}]
        def ask(model,system,user,**kwargs):
            return ('{"issues":[{"id":"1/Вт","dimension":"topic",'
                    '"reason":"Тема не соответствует цели исходного задания"}]}')
        with patch('plan_governance.propose_topic_replacement',
                   side_effect=AssertionError('first-pass mode must not replan')):
            result,blocked,available=plan_governance.govern_plan(
                ask,'model','request',{},'strategy',slots,set(),
                expected_ids=['1/Вт'],allow_replan=False)
        self.assertTrue(available)
        self.assertEqual(result,slots)
        self.assertIn('1/Вт',blocked)


if __name__=='__main__':
    unittest.main()
