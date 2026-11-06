"""Random task contracts and provider-isolation invariants (no model mocks as QA)."""
import copy
import random
import secrets
import unittest
import uuid

from execution_environment import isolated_contract_test_environment
from workflow_authority import ExecutionContract, assess_production, verify_ready_record, next_recovery_action
from completion_gate import can_close_task


class ExecutionAuthorityArchitecture(unittest.TestCase):
    def test_random_independent_tasks_cannot_accept_another_result(self):
        # New test cases on every run, with reproducible seeds in failure output.
        seed=secrets.randbits(64)
        rng=random.Random(seed)
        for i in range(100):
            with self.subTest(seed=seed,case=i):
                company=rng.choice(['школа', 'студия', 'пекарня', 'мастерская', 'кинотеатр'])
                platform=rng.choice(['Telegram','VK','Instagram'])
                weeks,cadence=rng.randint(1,15),rng.randint(1,5)
                text=f'{company} на {platform}: {weeks} недель, {cadence} публикаций, кейс {uuid.uuid4().hex}'
                original=ExecutionContract.create(uuid.uuid4().hex,text,weeks,cadence)
                other=ExecutionContract.create(uuid.uuid4().hex,text,weeks,cadence)
                self.assertTrue(original.matches(original.proof()))
                self.assertFalse(original.matches(other.proof()))
                changed=copy.deepcopy(original.proof())
                changed['scope']['planned_total']-=1
                self.assertFalse(original.matches(changed))
                changed=copy.deepcopy(original.proof())
                changed['request_sha256']='f'*64
                self.assertFalse(original.matches(changed))
                ids=[f'{week}/{day}' for week in range(1,weeks+1)
                     for day in range(1,cadence+1)]
                full=assess_production(ids,ids,plan_reviewed=True,editor_available=True)
                self.assertTrue(verify_ready_record(full.record(),len(ids)))
                partial=assess_production(ids,ids[:-1],plan_reviewed=True,editor_available=True)
                self.assertFalse(verify_ready_record(partial.record(),len(ids)))
                self.assertEqual(partial.code,'missing_outputs')
                self.assertEqual(next_recovery_action(partial),'resume_missing_outputs_only')
                unavailable=assess_production(ids,ids,plan_reviewed=True,editor_available=False)
                self.assertEqual(unavailable.state,'blocked_review')
                self.assertFalse(verify_ready_record(unavailable.record(),len(ids)))

    def test_review_outage_not_treated_as_bad_content_or_success(self):
        ids=['1/1','1/2']
        for state,code,kw in (
            ('blocked_review','plan_review_unavailable',{'plan_reviewed':False,'editor_available':True}),
            ('blocked_review','independent_review_unavailable',{'plan_reviewed':True,'editor_available':False}),
            ('blocked_content','editorial_findings',{'plan_reviewed':True,'editor_available':True,
                                                    'pending_findings':['unsupported claim']}),
            ('blocked_generation','generation_failed',{'plan_reviewed':True,'editor_available':True,
                                                      'production_error':'timeout'}),
        ):
            with self.subTest(code=code):
                record=assess_production(ids,ids,**kw)
                self.assertEqual(record.state,state)
                self.assertEqual(record.code,code)
                self.assertIn(next_recovery_action(record),{
                    'requalify_reviewer_then_resume_plan',
                    'requalify_reviewer_then_resume_review',
                    'repair_cited_artifacts_only',
                    'resume_unfinished_generation'})
                self.assertFalse(verify_ready_record(record.record(),2))

    def test_invalid_scope_or_foreign_slot_fails_closed(self):
        with self.assertRaises(ValueError):
            ExecutionContract.create(uuid.uuid4().hex,'request',True,1)
        with self.assertRaises(ValueError):
            assess_production(['1/1'],['2/1'],plan_reviewed=True,editor_available=True)

    def test_unit_process_cannot_inherit_paid_provider_or_qualified_reviewer(self):
        parent={'PATH':'/bin','AI_SMM_CLOUD_MODELS':'openai/gpt-6-luna',
                'OPENAI_API_KEY':'secret','AI_SMM_ENABLE_OPENAI_API':'1',
                'AI_SMM_ACCEPTANCE_REVIEWER_MODELS':'["openai/gpt-6-luna"]',
                'AI_SMM_REVIEW_QUALIFICATIONS':'signed',
                'AI_SMM_TEST_PROFILE':'full'}
        child=isolated_contract_test_environment(parent)
        self.assertEqual(child,{'PATH':'/bin','AI_SMM_TEST_PROFILE':'full'})
        self.assertIn('OPENAI_API_KEY',parent)
        self.assertIn('AI_SMM_ACCEPTANCE_REVIEWER_MODELS',parent)

    def test_true_persisted_bundle_requires_same_task_and_full_stage_evidence(self):
        from test_r93_bound_completion import delivery, parameters, REQUEST, STRATEGY, calendar
        from r94_bound_fixture import bind_report
        original=ExecutionContract.create(uuid.uuid4().hex,REQUEST,1,1)
        artifact={'strategy':STRATEGY,'calendar':calendar(1,1)}
        report=delivery(1,1)
        bind_report(report,REQUEST,artifact)
        report.update(task_id=original.task_id,request_sha256=original.request_sha256,
                      execution_contract=original.proof(),
                      workflow_assessment=assess_production(['1/1'],['1/1'],
                          plan_reviewed=True,editor_available=True).record())
        def verify(value, owner=original):
            return can_close_task({'strategy','calendar'},value,
                expected_parameters=parameters(1,1),request=REQUEST,
                artifacts=artifact,execution_contract=owner)
        self.assertTrue(verify(report))
        for field, bad in [
            ('task_id',uuid.uuid4().hex),
            ('request_sha256','0'*64),
            ('execution_contract',ExecutionContract.create(uuid.uuid4().hex,
                                      REQUEST,1,1).proof()),
            ('workflow_assessment',{'schema_version':1,'state':'ready_for_persistence',
                                      'code':'awaiting_storage_and_final_review',
                                      'expected':1,'accepted':0,'outstanding':[]})
        ]:
            with self.subTest(field=field):
                forged=copy.deepcopy(report);forged[field]=bad
                self.assertFalse(verify(forged))
        forged=copy.deepcopy(report);forged.pop('workflow_assessment')
        self.assertFalse(verify(forged))

    def test_legacy_delivery_cannot_claim_new_task_execution_contract(self):
        contract=ExecutionContract.create(uuid.uuid4().hex,'Strategy',1,2)
        self.assertFalse(can_close_task({'strategy','calendar'}, {}, request='Strategy',
                                        execution_contract=contract))


if __name__=='__main__':unittest.main()
