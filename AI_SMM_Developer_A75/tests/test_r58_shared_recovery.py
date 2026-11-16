import copy
import hashlib
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import compound_tasks as ct
import generic_text_review as review
import plan_governance as pg
from production_pipeline import run_document_task, _final_rejection_identity
from text_task_contract import TextRevisionContract
from workflow_recovery import capture_failures, verified_failures, bind_recovery_report
from test_stage69_scope import good_post, mini_plan


class SharedRecoveryTests(unittest.TestCase):
    def partial(self, slots, accepted=()):
        signature=hashlib.sha256(b'arbitrary request').hexdigest()
        records=capture_failures(signature,[f"1/{s['day']}" for s in slots],slots,
            {'1/Вт':'Specific objective mismatch'},True,{},set(accepted))
        return {'action':'compound_documents','task_signature':signature,
            'documents':[{'kind':'strategy','content':'Exact strategy.','valid':True},
                         {'kind':'calendar','content':'No completed rows.','valid':False}],
            'content_delivery':{'scope':{'weeks':1,'per_week':2,'planned_total':2},
                'plan_preflight':{'reviewer_available':True,'unresolved_slots':['1/Вт']}},
            'workflow_failures':records}

    def test_varied_stage_findings_are_bound_to_exact_slot_and_artifact(self):
        rng=random.Random(5801)
        for index in range(100):
            token=''.join(rng.choice('abcабв🐾') for _ in range(30))
            slots=[{'week':1,'day':'Вт','topic':token},
                   {'week':1,'day':'Пт','topic':'Other '+token}]
            result=self.partial(slots)
            self.assertEqual(len(verified_failures(result)),1)
            bound=bind_recovery_report(result)
            self.assertEqual(bound['content_delivery']['independent_editor']['repair_slots'],['1/Вт'])
            self.assertEqual(bound['content_delivery']['independent_editor']['issues'][0]['quote'],token)
            self.assertNotIn('independent_editor',result['content_delivery'])
            altered=copy.deepcopy(result)
            altered['workflow_failures'][0]['artifact']['topic']='Different bytes'
            self.assertEqual(verified_failures(altered),[])
            wrong=copy.deepcopy(result);wrong['workflow_failures'][0]['task_signature']='0'*64
            self.assertEqual(verified_failures(wrong),[])
            accepted=self.partial(slots,{'1/Вт'})
            self.assertEqual(accepted['workflow_failures'],[])

    def test_unavailable_review_never_authorizes_author_rewrite(self):
        signature='1'*64
        rows=capture_failures(signature,['1/Вт'],[{'week':1,'day':'Вт','topic':'A'}],
            {'1/Вт':'Unavailable'},False,{},set())
        self.assertEqual(rows,[])
        result=self.partial([{'week':1,'day':'Вт','topic':'A'},{'week':1,'day':'Пт','topic':'B'}])
        result['content_delivery']['plan_preflight']['reviewer_available']=False
        self.assertEqual(verified_failures(result),[])

    def test_malformed_recovery_envelopes_cannot_turn_into_authorization(self):
        for value in (None,{}, {'task_signature':'1'*64,'content_delivery':None},
                      {'task_signature':'1'*64,'content_delivery':{'scope':None}},
                      {'task_signature':'z'*64,'content_delivery':{}}):
            self.assertEqual(verified_failures(value),[])

    def test_publication_failure_keeps_typed_findings_and_accepted_sibling(self):
        post={'id':'1/Вт','caption':'Exact rejected draft'}
        issue={'quote':'Exact rejected draft','reason':'Specific factual defect','field_type':'caption'}
        rejected={'1/Вт':{'post':post,'issues':[issue]},
                  '1/Пт':{'post':{'id':'1/Пт','caption':'Accepted bytes'},'issues':[issue]}}
        records=capture_failures('1'*64,['1/Вт','1/Пт'],[],{},True,rejected,{'1/Пт'})
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['stage'],'publication')
        self.assertEqual(records[0]['artifact']['issues'],[issue])
        self.assertEqual(records[0]['artifact']['post'],post)
        self.assertEqual(rejected['1/Пт']['post']['caption'],'Accepted bytes')

    def test_plan_change_counts_as_progress_even_before_any_new_calendar_row(self):
        first=self.partial([{'week':1,'day':'Вт','topic':'A'},{'week':1,'day':'Пт','topic':'B'}])
        second=self.partial([{'week':1,'day':'Вт','topic':'C'},{'week':1,'day':'Пт','topic':'B'}])
        a=bind_recovery_report(first);b=bind_recovery_report(second)
        self.assertNotEqual(_final_rejection_identity(a,a['content_delivery']['independent_editor']),
                            _final_rejection_identity(b,b['content_delivery']['independent_editor']))

    def test_actual_compound_recovery_preserves_analysis_strategy_and_accepted_post(self):
        request='Разработай SMM-стратегию и контент-план на 1 неделю, 2 публикации в неделю, всего 2 публикации для Instagram.'
        strategy='# Стратегия\n'+('Цели. Аудитория. Каналы. Метрики. План действий. '*25)
        calls=[];executions=[];writes=[];accepted_snapshot=[]
        def checked_strategy(*args,**kwargs):
            calls.append(kwargs.get('existing'))
            if len(calls)>1:self.assertEqual(kwargs['existing'],strategy)
            return strategy,True,''
        def govern(ask,model,req,brief,body,slots,accepted_ids,**kwargs):
            self.assertEqual(body,strategy)
            if len(executions)==1:
                self.assertFalse(kwargs.get('allow_replan',True))
                return slots,{'1/Вт':'The topic misses the requested objective'},True
            self.assertEqual(kwargs['repair_targets'],{'1/Вт'})
            self.assertEqual(kwargs['max_candidates'],1)
            self.assertIn('1/Пт',accepted_ids)
            result=copy.deepcopy(slots);result[0]['topic']='Revised objective-specific topic'
            return result,{},True
        def writer(ask,model,req,known,body,slots,*args,**kwargs):
            for slot in slots:writes.append((len(executions),f"{slot['week']}/{slot['day']}"))
            return [good_post(slot) for slot in slots]
        def single(ask,model,req,known,body,slot,*args,**kwargs):
            writes.append((len(executions),f"{slot['week']}/{slot['day']}"))
            return good_post(slot)
        def plan(*args,**kwargs):
            kwargs.pop('first_pass_only',None)
            return mini_plan(*args,**kwargs)
        with tempfile.TemporaryDirectory() as home:
            def execute(req,state,model,progress=None):
                executions.append(copy.copy(state))
                result=ct.execute_compound(req,{'plan_reviewer_model':'independent-reviewer', **(state)},model=model,progress=progress)
                checkpoints=list((Path(home)/'drafts').glob('publication_progress_*.json'))
                if checkpoints:
                    posts=json.loads(checkpoints[0].read_text(encoding='utf-8'))['posts']
                    if '1/Пт' in posts:accepted_snapshot.append(copy.deepcopy(posts['1/Пт']))
                return result
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'explicit'}) as analysis, \
                 patch.object(ct,'produce_checked_strategy',side_effect=checked_strategy), \
                 patch.object(ct,'plan_slots',side_effect=plan), \
                 patch.object(ct,'govern_plan',side_effect=govern), \
                 patch.object(ct,'write_week',side_effect=writer), \
                 patch.object(ct,'write_single',side_effect=single), \
                 patch.object(ct,'audit_post',side_effect=lambda a,m,r,k,s,p,*v:(p,[])), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                # The synthetic mode measures one initial pass plus one correction.
                result=run_document_task(request,{'project_workspace':home,
                    'randomized_acceptance_run':True,'acceptance_execution_mode':'bounded_workflow',
                    'quality_model_pool':['writer','reviser','reviewer']},
                    execute=execute,model='writer',review_model='reviewer',
                    strategy_reviewer=lambda *a,**k:{'pass':True,'issues':[]},
                    final_reviewer=lambda *a,**k:{'pass':True,'issues':[]})
            self.assertEqual(len(executions),2,result)
            self.assertEqual(analysis.call_count,1)
            self.assertEqual(executions[1]['task_mode'],'final_review_repair')
            self.assertEqual(accepted_snapshot[0],accepted_snapshot[-1])
            self.assertNotIn((1,'1/Вт'),writes)
            self.assertEqual(sum(slot=='1/Пт' for _,slot in writes),1)
            self.assertEqual(result['documents'][0]['content'],strategy)
            self.assertEqual(result['content_delivery'].get('independent_editor',{}).get('status'),'PASS',
                {'delivery':result['content_delivery'],'documents':[(d['kind'],d['valid'],d.get('reason')) for d in result['documents']]})

    def test_author_task_is_not_replayed_before_targeted_stage_recovery(self):
        from compound_retry import run_compound_with_retry
        partial=self.partial([{'week':1,'day':'Вт','topic':'A'},{'week':1,'day':'Пт','topic':'B'}])
        calls=[]
        def execute(*args,**kwargs):calls.append(1);return partial
        result=run_compound_with_retry(execute,'request',{},max_attempts=3)
        self.assertEqual(len(calls),1)
        self.assertIs(result,partial)

    def test_plan_correction_has_one_candidate_and_cannot_touch_other_slots(self):
        slots=[dict(week=1,day=day,topic='Wrong '+day,brief='Brief',goal='Goal',
                    audience='Readers',format='Post',platform='Instagram') for day in ('Вт','Пт')]
        def ask(model,system,user,**kwargs):
            obj=json.loads(user)
            if 'candidate' in obj:return '{"issues":[]}'
            return json.dumps({'issues':[{'id':f"1/{s['day']}",'dimension':'topic',
                'reason':'Objective mismatch requiring a topic correction'} for s in obj['plan']
                if s['topic'].startswith('Wrong')]})
        alternatives=[{**slots[0],'topic':'First candidate'},{**slots[0],'topic':'Second candidate'}]
        with patch.object(pg,'propose_topic_replacement',return_value=alternatives) as propose:
            new,blocked,available=pg.govern_plan(ask,'author','Create a varied plan',{},'',slots,set(),
                expected_ids=['1/Вт','1/Пт'],review_model='judge',repair_targets={'1/Вт'},max_candidates=1)
        self.assertEqual(propose.call_count,1)
        self.assertTrue(available)
        self.assertEqual(new[0]['topic'],'First candidate')
        self.assertEqual(new[1],slots[1])
        self.assertIn('1/Пт',blocked)

    def test_bad_change_report_does_not_replace_actual_independent_review(self):
        draft='Unverified promise. Useful paragraph.'
        revised='General explanation. Useful paragraph.'
        contract=TextRevisionContract.create(draft,[{'quote':'Unverified promise.',
            'check':'factual_grounding','reason':'No supporting fact'}])
        self.assertEqual(contract.validate({'revised_text':revised,'resolutions':{
            'invented_id':{'action':'rewritten','revised_quote':'Not in text'}}}),revised)
        self.assertEqual(contract.evidence(revised)['semantic_resolution'],'requires_independent_review')
        retained=contract.validate({'revised_text':draft+' More.'})
        self.assertTrue(contract.evidence(retained)['findings'][0]['original_quote_retained'])
        self.assertEqual(contract.evidence(retained)['semantic_resolution'],'requires_independent_review')
        reviews=[]
        def judge(req,text,ctx,**kw):
            reviews.append(text)
            return {'pass':False,'issues':[{'quote':text,'check':'requirements',
                    'reason':'Still misses the requested result'}],'reviewer_models':{}}
        result=review.run_quality_cycle('Task',{'action':'write','answer':draft},{},
            reviewer=judge,repairer=lambda *a,**k:revised)
        self.assertEqual(reviews,[draft,revised])
        self.assertEqual(result['action'],'write_blocked')
        self.assertEqual(result['answer'],revised)

    def test_revision_is_retained_if_its_review_fails_and_old_verdict_cannot_approve_it(self):
        calls=[]
        def judge(req,text,ctx,**kw):
            calls.append(text)
            if len(calls)==2:raise RuntimeError('Review transport unavailable')
            return {'pass':False,'issues':[{'quote':text,'check':'requirements','reason':'Missing useful element'}]}
        result=review.run_quality_cycle('Task',{'action':'write','answer':'Old draft'},{},
            reviewer=judge,repairer=lambda *a,**k:'Corrected draft')
        self.assertEqual(result['answer'],'Corrected draft')
        self.assertEqual(result['action'],'write_blocked')
        self.assertEqual(result['quality_review']['original_draft'],'Old draft')
        self.assertEqual(result['quality_review']['review_state'],'unavailable')
        self.assertFalse(any(result['quality_review']['checks'].values()))

    def test_cloud_source_role_preference_respects_independence_and_allow_exclude(self):
        pool=['writer:1','judgeA:1','judgeB:1','judgeC:1','judgeD:1','cloudjudge:cloud']
        for seed in range(20):
            roles=review.select_reviewer_models('writer:1',candidates=pool,chooser=random.Random(seed))
            self.assertEqual(roles['factual_grounding'],'cloudjudge:cloud')
            self.assertEqual(len(set(roles.values())),4)
            self.assertNotIn('writer:1',roles.values())
        with patch.object(review,'configured_model_pool',return_value={'allowlist':[], 'exclude':['cloudjudge:cloud']}):
            roles=review.select_reviewer_models('writer:1',candidates=pool)
        self.assertNotIn('cloudjudge:cloud',roles.values())

    def test_style_findings_require_a_request_or_intrinsic_quality_binding(self):
        seen=[]
        def chat(payload,**kwargs):
            obj=json.loads(payload['messages'][1]['content']);seen.append(obj)
            return {'message':{'content':json.dumps({'pass':False,'issues':[{
                'evidence_id':obj['review_evidence'][0]['evidence_id'],'reason':'The copied task instructions are not reader-facing prose',
                'severity':'major','requirement_id':'G_editorial_meta','failure_type':'style_mismatch'}]})}}
        with patch.object(review,'request_chat',side_effect=chat):
            result=review.review_dimension('Create a complete public post','Draft',{},'format_and_tone','judge')
        self.assertFalse(result['pass'])
        self.assertEqual(result['issues'][0]['requirement_mode'],'quality_policy')
        self.assertTrue(any(b['id']=='G_editorial_meta' for b in seen[0]['requirement_bindings']))


if __name__=='__main__':unittest.main()
