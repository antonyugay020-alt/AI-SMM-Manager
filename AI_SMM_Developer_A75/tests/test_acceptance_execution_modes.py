"""Host-selected test mode must not silently disable production recovery.

This verifies control flow with isolated in-process executors, not live model
quality or a Windows GUI pass. Every run also generates fresh task identities.
"""
import random
import unittest
from uuid import uuid4
from unittest.mock import patch

from acceptance_policy import (is_first_pass_acceptance, is_full_delivery_acceptance,
                               attempt_limit, final_repair_limit, workflow_outcome_metrics)
from production_pipeline import run_document_task


class AcceptanceExecutionModeTests(unittest.TestCase):
    def test_first_pass_and_full_delivery_are_distinct_for_new_tasks(self):
        for _ in range(30):
            ident=uuid4().hex
            brief=f'{ident} Публикации для {random.choice(("пекарни","галереи","мастерской","школы"))}'
            fast={'task_id':ident,'request':brief,'randomized_acceptance_run':True,
                  'acceptance_execution_mode':'bounded_workflow'}
            full={**fast,'acceptance_execution_mode':'full_delivery'}
            self.assertTrue(is_first_pass_acceptance(fast))
            self.assertFalse(is_full_delivery_acceptance(fast))
            self.assertEqual(attempt_limit(fast,7),1)
            self.assertEqual(final_repair_limit(fast,2),1)
            self.assertFalse(is_first_pass_acceptance(full))
            self.assertTrue(is_full_delivery_acceptance(full))
            self.assertEqual(attempt_limit(full,7),7)
            self.assertEqual(final_repair_limit(full,2),2)
            # The normal user interaction remains production mode regardless of
            # a stale value of acceptance_execution_mode.
            self.assertFalse(is_full_delivery_acceptance({**full,'randomized_acceptance_run':False}))

    def test_executor_receives_mode_specific_bounded_retry_budget(self):
        for mode,expected in [('bounded_workflow',1),('full_delivery',2)]:
            with self.subTest(mode=mode):
                calls=[]
                def fake(execute,request,context,*,progress,max_attempts,model):
                    calls.append((max_attempts,context['task_id'],request))
                    return {'action':'compound_documents','instruction':request,
                            'documents':[], 'error':'content_not_ready'}
                task_id=uuid4().hex
                with patch('production_pipeline.run_compound_with_retry',side_effect=fake):
                    result=run_document_task('Задание '+task_id,
                        {'task_id':task_id,'randomized_acceptance_run':True,
                         'acceptance_execution_mode':mode},
                        execute=lambda *a,**k:None,
                        strategy_reviewer=lambda *a:{'pass':True,'issues':[]},
                        final_reviewer=lambda *a:{'pass':True,'issues':[]},
                        review_model='independent:1')
                self.assertEqual(calls,[(expected,task_id,'Задание '+task_id)])
                self.assertEqual(result['error'],'content_not_ready')

    def test_first_pass_and_delivery_verdicts_remain_distinct(self):
        events=[{'stage':'final_review_result','attempt':1,'status':'BLOCKED'},
                {'stage':'autonomous_retry','attempt':2},
                {'stage':'final_review_result','attempt':2,'status':'PASS'}]
        outcome=workflow_outcome_metrics('compound_strategy_calendar',events,
                                          execution_mode='full_delivery')
        self.assertEqual(outcome['execution_mode'],'full_delivery')
        self.assertEqual(outcome['first_pass_status'],'FAIL')
        self.assertTrue(outcome['targeted_repair_applied'])
        with self.assertRaises(ValueError):
            workflow_outcome_metrics('compound_strategy_calendar',events,
                                     execution_mode='unrecognized')


if __name__=='__main__':
    unittest.main()
