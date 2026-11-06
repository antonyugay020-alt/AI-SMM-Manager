"""Repair boundaries across real executors; model outputs are injected."""
import copy
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import compound_tasks as ct
from claim_interpretation import bind_interpretation
from claim_support import adjudicate_factual_findings
from production_pipeline import run_document_task
from publication_writer import repair_fields, repair_single, repeated_rejected_claims, format_repair_issues
from workflow_recovery import retain_rejected_draft
from test_stage69_scope import good_post, mini_plan
import test_windows_acceptance_v4 as fixtures
from test_r62_review_boundaries import with_changes, public_copy_findings


class ConfirmedInstructions(unittest.TestCase):
    def test_arbitrary_confirmed_edit_reaches_both_writer_paths_without_aliasing(self):
        rng=random.Random(7101)
        for index in range(12):
            token=''.join(rng.choice('abcабв') for _ in range(20))
            slot={'week':1,'day':'Вт','topic':'An arbitrary topic '+token}
            post=good_post(slot)
            issue={'field_type':'cta','quote':post['cta'],'reason':'The requested next step is not represented.',
                'finding_validation':{'decision':'confirmed','required_change':'Ask for the next step '+token}}
            snapshot=copy.deepcopy(issue);payloads=[]
            def ask(model,system,user,**kw):
                data=json.loads(user);payloads.append(data)
                if 'revision_spans' in data:
                    return json.dumps({'replacements':{row['id']:'A concrete reader question '+token for row in data['revision_spans']}})
                return json.dumps({**post,'cta':'A concrete reader question '+token})
            out=repair_fields(ask,'writer','Original brief',{},'',slot,post,[issue])
            self.assertEqual(out['caption'],post['caption'])
            self.assertNotEqual(out['cta'],post['cta'])
            repair_single(ask,'writer','Original brief',{},'',slot,post,[issue])
            for payload in payloads:
                self.assertIn(issue['finding_validation']['required_change'],json.dumps(payload,ensure_ascii=False))
            self.assertEqual(issue,snapshot)
    def test_unconfirmed_proposal_is_not_promoted_to_confirmed_instruction(self):
        issue={'reason':'Unverified allegation','finding_validation':{'decision':'uncertain','required_change':'Not authorized'}}
        self.assertNotIn('Not authorized',format_repair_issues([issue])[0])
    def test_literal_retention_is_only_a_hint_and_paraphrases_are_not_keyword_verdicts(self):
        issue={'quote':'Original disputed sentence.','statement_class':'service_outcome'}
        self.assertTrue(repeated_rejected_claims({'caption':'Original disputed sentence. Unchanged useful detail.'},[issue]))
        self.assertFalse(repeated_rejected_claims({'caption':'A different statement containing arbitrary vocabulary.'},[issue]))


class RecoverySnapshot(unittest.TestCase):
    def pair(self):
        previous=fixtures.TaskRecoveryTests.result(True);previous['task_signature']='7'*64
        previous['documents'][1]['valid']=False
        previous['content_delivery']['independent_editor']={'status':'BLOCKED','review_state':'rejected',
            'issues':[{'document':'calendar','quote':'полный документ','reason':'Unresolved source claim'}]}
        candidate=copy.deepcopy(previous)
        candidate['content_delivery']['checked_posts']=0
        candidate['documents'][1].update(content='An empty schedule.',valid=False)
        return previous,candidate
    def test_failed_replacement_keeps_full_draft_and_exact_rejection_with_both_versions(self):
        previous,candidate=self.pair();snapshot=copy.deepcopy(previous)
        out,kept=retain_rejected_draft(previous,candidate,1)
        self.assertTrue(kept);self.assertEqual(out['documents'],previous['documents'])
        self.assertEqual(out['content_delivery']['independent_editor'],previous['content_delivery']['independent_editor'])
        self.assertEqual(out['failed_repair_attempts'][0]['replacement']['documents'],candidate['documents'])
        out['failed_repair_attempts'][0]['replacement']['documents'].clear()
        self.assertEqual(previous,snapshot);self.assertTrue(candidate['documents'])
    def test_unrelated_or_malformed_counts_never_restore_another_task(self):
        previous,candidate=self.pair()
        for change in ({'task_signature':'8'*64},{'task_signature':None},
                       {'content_delivery':None},{'content_delivery':{'checked_posts':False}},
                       {'content_delivery':{'checked_posts':-1}}):
            changed={**candidate,**change}
            self.assertFalse(retain_rejected_draft(previous,changed,1)[1])
    def test_successful_replacement_and_unchanged_counts_are_not_rolled_back(self):
        previous,candidate=self.pair();candidate['content_delivery']['checked_posts']=2
        candidate['content_delivery']['independent_editor']={'status':'PASS'}
        out,kept=retain_rejected_draft(previous,candidate,1)
        self.assertFalse(kept);self.assertIs(out,candidate)
    def test_actual_pipeline_retains_rejection_and_persists_failed_replacement(self):
        previous,candidate=self.pair();seen=[];events=[]
        # The normal final gate establishes the original rejection first.
        original=copy.deepcopy(previous);original['documents'][1]['valid']=True
        original['content_delivery'].pop('independent_editor')
        from publication_writer import week_to_markdown
        slot={'week':1,'day':'Вт','topic':'Arbitrary topic','platform':'Instagram','format':'Пост','goal':'Explanation'}
        post=good_post(slot)
        original['documents'][1]['content']=week_to_markdown([post],[slot])
        def execute(req,state,**kwargs):
            seen.append(state);return copy.deepcopy(original if len(seen)==1 else candidate)
        issue={'document':'calendar','type':'invented_fact','quote':post['cta'],
            'reason':'An unsupported assertion remains in the current row.', 'row_context':{'slot':'1/Вт'}}
        with tempfile.TemporaryDirectory() as home:
            result=run_document_task('Prepare a strategy and calendar.',{'project_workspace':home},
                execute=execute,model='writer',review_model='reader',progress=events.append,
                strategy_reviewer=lambda *a,**k:{'pass':True,'issues':[]},
                final_reviewer=lambda *a,**k:{'pass':False,'issues':[issue]})
            self.assertEqual(len(seen),2,(events,result))
            self.assertEqual(result['documents'][1]['content'],original['documents'][1]['content'])
            self.assertFalse(result['documents'][1]['valid'])
            self.assertEqual(result['content_delivery']['independent_editor']['status'],'BLOCKED')
            self.assertTrue(result['failed_repair_attempts'])
            saved=json.loads(next((Path(home)/'drafts').glob('content_delivery_*.json')).read_text())
            self.assertEqual(saved['failed_repair_attempts'],result['failed_repair_attempts'])
            self.assertTrue(any(e['stage']=='failed_repair_draft_retained' for e in events))


class PublicationReviewBoundary(unittest.TestCase):
    def run_repair(self,reject):
        request='Разработай SMM-стратегию и контент-план на 1 неделю, 2 публикации в неделю, всего 2 публикации для Instagram.'
        strategy='# Стратегия\n'+('Цели. Аудитория. Каналы. Метрики. План действий. '*25)
        reviewed=[];events=[];recovery=[False]
        def plan(*args,**kw):kw.pop('first_pass_only',None);return mini_plan(*args,**kw)
        def audit(a,m,r,k,s,p,*extra):
            if recovery[0]:reviewed.append(copy.deepcopy(p))
            return p,(['Unresolved source assertion'] if recovery[0] and reject else [])
        def repair(a,m,r,k,b,s,p,issues,*extra):return {**p,'cta':'A new concrete question to the reader.'}
        with tempfile.TemporaryDirectory() as home, \
             patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'explicit'}), \
             patch.object(ct,'produce_checked_strategy',return_value=(strategy,True,'')), \
             patch.object(ct,'plan_slots',side_effect=plan), \
             patch.object(ct,'govern_plan',side_effect=lambda a,m,r,b,d,slots,*v,**kw:(slots,{},True)), \
             patch.object(ct,'write_week',side_effect=lambda a,m,r,k,b,slots,*v,**kw:[good_post(s) for s in slots]), \
             patch.object(ct,'repair_fields',side_effect=repair), \
             patch.object(ct,'audit_post',side_effect=audit), \
             patch.object(ct,'copy_too_similar',return_value=False), \
             patch.object(ct,'editorial_audit',return_value=[]):
            state={'project_workspace':home,'randomized_acceptance_run':True,'acceptance_execution_mode':'bounded_workflow',
                   'plan_reviewer_model':'reader','publication_reviewer_models':['reader','backup']}
            initial=ct.execute_compound(request,state,model='writer')
            recovery[0]=True
            issues={ident:[{'field_type':'cta','quote':'Сохраните чек-лист до следующего выбора.',
                'reason':'A different next step is required.'}] for ident in ('1/Вт','1/Пт')}
            result=ct.execute_compound(request,{**state,'task_mode':'final_review_repair',
                'final_review_repair_slots':list(issues),'final_review_issues_by_slot':issues},
                model='writer',progress=events.append)
            self.assertEqual(initial['content_delivery']['checked_posts'],2,initial)
            self.assertEqual(len(reviewed),2)
            for post in reviewed:self.assertEqual(post['caption'],good_post({'week':1,'day':post['id'].split('/')[1],'topic':post['topic']})['caption'])
            return result,events
    def test_same_caption_and_changed_cta_reaches_real_executor_independent_gate(self):
        result,events=self.run_repair(False)
        self.assertEqual(result['content_delivery']['checked_posts'],2)
        self.assertFalse(any(e['stage']=='single_quality_rejected' for e in events))
    def test_retained_defect_still_fails_independent_gate_after_lexical_veto_removal(self):
        result,events=self.run_repair(True)
        self.assertEqual(result['content_delivery']['checked_posts'],0)
        self.assertTrue(any(e['stage']=='single_semantic_rejected' for e in events))


class SourceRoleConsistency(unittest.TestCase):
    def test_conflicting_roles_require_correction_instead_of_inventing_a_defect(self):
        value=with_changes(speech_act='invitation',assertion_subject='reader',invitation_kind='communication',
            source_relation='not_needed',verdict='supported')
        with self.assertRaises(ValueError):bind_interpretation(value,require=True,business_scope=True)
    def test_uncertainty_stays_uncertain_and_cannot_approve_business_claim(self):
        value=with_changes(speech_act='uncertain',assertion_subject='unclear',verdict='supported')
        out=bind_interpretation(value,require=True,business_scope=True)
        self.assertEqual(out['verdict'],'uncertain')
    def test_real_source_adjudicator_corrects_role_conflict_on_identical_evidence(self):
        replies=[]
        def ask(m,s,u,**kw):
            replies.append(json.loads(u))
            decision=with_changes(speech_act='invitation',assertion_subject='reader',invitation_kind='communication',
                source_relation='not_needed',verdict='supported') if len(replies)==1 else with_changes(
                statement_class='business_fact',speech_act='assertion',assertion_subject='business',
                invitation_kind='none',source_relation='unrelated',verdict='unsupported')
            return {'decisions':{'F001':decision}}
        findings=public_copy_findings('Мы выполним подтверждённое действие.')
        out=adjudicate_factual_findings('Write.',findings,ask=ask,require_semantic_contract=True)
        self.assertEqual(len(replies),2)
        self.assertEqual(replies[0],replies[1])
        self.assertEqual(out['blocking'][0]['adjudication'],'unsupported')


class CoverageBoundary(unittest.TestCase):
    def report(self):
        return {'cases':[{'name':'first','author_model':'writer'},{'name':'second','author_model':'writer'}],
            'task_generation':{'case_count':2,'selected_author_model_count':1,'author_models_tested':['writer']},
            'model_comparison':[{'model':'writer'}]}
    def test_one_author_two_tasks_reaches_final_windows_evidence_validator(self):
        from windows_acceptance import workflow_coverage_errors
        self.assertEqual(workflow_coverage_errors(self.report()),[])
    def test_wrong_task_author_and_comparison_counts_still_block(self):
        from windows_acceptance import workflow_coverage_errors
        for change in ({'case_count':1},{'selected_author_model_count':2},{'case_count':True},
                       {'author_models_tested':['writer','writer']}):
            data=self.report();data['task_generation'].update(change)
            self.assertTrue(workflow_coverage_errors(data))
        data=self.report();data['cases'][0]['author_model']='other'
        self.assertTrue(workflow_coverage_errors(data))
        data=self.report();data['model_comparison'].append({'model':'writer'})
        self.assertTrue(workflow_coverage_errors(data))
        for bad in ({'author_model':[]},{'name':[]},{'name':'first'}):
            data=self.report();data['cases'][1].update(bad)
            self.assertTrue(workflow_coverage_errors(data))
