"""Preserve all repair targets and actual independent reviewer identities."""
import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import independent_acceptance_editor as editor
import production_pipeline as pipeline
from completion_gate import can_close_task
from independent_quality_gate import final_quality_gate
from substance_fixture import assessed_reply
from test_r81_typed_windows import documents


def bundle(st,ca,n):
    return {'action':'compound_documents','documents':[{'kind':'strategy','content':st,'valid':True},{'kind':'calendar','content':ca,'valid':True}],
        'content_delivery':{'unfulfilled':[],'rendered_media':'not_requested','deliverables':{'strategy':'validated','calendar':'validated'},
            'scope':{'weeks':n,'per_week':1,'planned_total':n},'checked_posts':n,
            'content_components_checked':dict.fromkeys(('caption','visual','cta','kpi','audience','measurement','materials'),n),
            'plan_preflight':{'reviewer_available':True,'unresolved_slots':[]},'task_orchestration':{'completed':True}}}


class CompleteRepairTargets(unittest.TestCase):
    def run_targets(self,n,partial=False):
        st,ca=documents(seed=8300+n,rows=n);data=bundle(st,ca,n);captions={}
        for row in editor._inventory_segments(st,ca):
            if row['field_type']=='caption':captions.setdefault(row['row_context']['slot'],row)
        issues=[{'document':'calendar','type':'quality','evidence_id':r['evidence_id'],'quote':r['content'],
            'row_context':r['row_context'],'field_type':'caption','reason':'The caption does not supply the promised explanation.',
            'finding_validation':{'decision':'confirmed','required_change':'Supply the promised explanation.'}} for r in captions.values()]
        if partial:issues.append({'document':'review','type':'review_unavailable','quote':'','reason':'Another window is unavailable.'})
        before=copy.deepcopy(data)
        result=final_quality_gate(data,'Prepare strategy and calendar.',lambda *a:editor.ValidatedEditorialVerdict(
            {'pass':False,'issues':issues,'review_state':'unavailable' if partial else 'complete'}))
        audit=result['content_delivery']['independent_editor'];self.assertEqual(len(audit['issues']),n)
        self.assertEqual(set(audit['repair_slots']),set(captions));self.assertEqual(set(audit['repair_issues_by_slot']),set(captions))
        self.assertEqual(data,before);issues[0]['reason']='Caller mutation';self.assertNotEqual(audit['issues'][0]['reason'],'Caller mutation')
        return audit

    def test_every_confirmed_target_survives_gate_mapping_and_copy(self):
        for n in (9,16,25):
            with self.subTest(n=n):self.run_targets(n)

    def test_final_repair_checkpoint_retains_all_findings_for_one_publication(self):
        import compound_tasks as ct
        request='Разработай стратегию и контент-план, Instagram, 1 неделя, 1 публикация в неделю.'
        class StopAfterReopening(Exception):pass
        with tempfile.TemporaryDirectory() as home:
            drafts=Path(home)/'drafts';drafts.mkdir()
            sig=hashlib.sha256((home+'\n'+request+'\n1\n1').encode()).hexdigest()
            post=dict.fromkeys(('topic','caption','visual','cta','kpi','audience','measurement','materials'),'A populated field.')
            post['id']='1/Ср'
            original=json.dumps({'signature':sig,'posts':{'1/Ср':post}}).encode()
            progress=drafts/('publication_progress_'+sig[:16]+'.json');progress.write_bytes(original)
            issues=[{'document':'calendar','type':'quality','quote':post['caption'],'reason':'Confirmed issue '+str(i),
                     'finding_validation':{'decision':'confirmed'}} for i in range(15)]
            with patch.object(ct,'extract_facts',side_effect=StopAfterReopening),self.assertRaises(StopAfterReopening):
                ct.execute_compound(request,{'project_workspace':home,'task_mode':'final_review_repair',
                    'final_review_repair_slots':['1/Ср'],'final_review_issues_by_slot':{'1/Ср':issues}})
            repair=json.loads((drafts/('publication_repair_'+hashlib.sha256(sig.encode('utf-8')).hexdigest()+'.json')).read_text(encoding='utf-8'))
            self.assertEqual(repair['drafts']['1/Ср']['issues'],issues)
            backup=next(drafts.glob('*.final_review_backup.json'));self.assertEqual(backup.read_bytes(),original)

    def test_partial_audit_keeps_every_separately_confirmed_target(self):
        audit=self.run_targets(12,partial=True);self.assertEqual(audit['review_state'],'unavailable');self.assertTrue(audit['validation_diagnostics'])


class ActualReviewVoices(unittest.TestCase):
    def run_case(self,spare=None,spare_disagrees=False):
        st,ca=documents(rows=2);calls=[];selectors=[];names=['reader_primary','reader_secondary']
        def select(author,**kw):
            selectors.append((author,kw))
            if names:return names.pop(0)
            if spare:return spare
            raise ValueError('No additional qualified family')
        def chat(payload,**kw):
            calls.append(payload['model']);schema=payload['format']
            if 'assessments' in schema.get('properties',{}):
                answer={'pass':True,'issues':[]}
                manifest=json.loads(payload['messages'][1]['content'].split('QUALITY_CRITERIA_JSON:\n')[-1])
                if payload['model']=='reader_secondary' or spare_disagrees and payload['model']==spare:
                    key,row=next((k,r) for k,r in manifest['checks'].items() if r['criterion']=='topic_delivery')
                    answer={'pass':False,'issues':[{'document':'calendar','type':'quality','criterion_id':key,'evidence_id':row['evidence_ids'][0],
                        'reason':'A disputed allegation of missing topic explanation.'}]}
                answer=assessed_reply(payload,answer)
            else:
                data=json.loads(payload['messages'][1]['content'])
                if 'review_evidence' in data:return {'message':{'content':'{"pass":true,"issues":[]}'}}
                answer={'decisions':{r['id']:{'action':'keep_artifact','remedy_scope':'none','required_change':'',
                    'reason':'The complete caption already contains the promised explanation.'} for r in data['review_findings']}}
            return {'message':{'content':json.dumps(answer)}}
        with patch('compound_tasks.execute_compound',return_value=bundle(st,ca,2)),patch('generic_text_review.select_independent_model',side_effect=select), \
             patch('ollama_resilience.request_chat',side_effect=chat),patch('generic_text_review.request_chat',side_effect=chat), \
             patch.object(editor,'_inventory_business_assertions',return_value=[]),patch.object(editor,'request_chat',side_effect=chat):
            out=pipeline.run_document_task('Create a strategy and calendar.',{'randomized_acceptance_run':True,
                'acceptance_execution_mode':'first_pass','acceptance_profile':'fast'},model='separate_author')
        return out['content_delivery']['independent_editor'],calls,selectors

    def test_same_actual_voice_after_dismissal_is_not_a_second_positive_opinion(self):
        audit,calls,selectors=self.run_case();self.assertEqual(audit['status'],'BLOCKED',audit)
        self.assertEqual(audit['review_state'],'unavailable');reading=audit['independent_reader']
        self.assertEqual(reading['model'],'reader_primary');self.assertFalse(reading['independence_verified'])
        self.assertEqual(reading['requested_model'],'reader_secondary');self.assertEqual(len(selectors),3)

    def test_one_new_qualified_family_supplies_the_missing_independent_reading(self):
        audit,calls,selectors=self.run_case(spare='reader_third');self.assertEqual(audit['status'],'PASS',audit)
        reading=audit['independent_reader'];self.assertEqual(reading['model'],'reader_third');self.assertTrue(reading['independence_verified'])
        self.assertEqual(reading['independent_from'],'reader_primary');self.assertIn('reader_third',calls);self.assertEqual(len(selectors),3)
        self.assertTrue({'separate_author','reader_primary','reader_secondary'}<=set(selectors[-1][1]['exclude_models']))

    def test_spare_disagreement_is_bounded_without_retry_until_pass(self):
        audit,calls,selectors=self.run_case(spare='reader_third',spare_disagrees=True)
        self.assertEqual(audit['status'],'BLOCKED');self.assertEqual(len(selectors),3);self.assertEqual(calls.count('reader_third'),1)

    def test_completion_requires_different_actual_families(self):
        st,ca=documents(rows=2);delivery=bundle(st,ca,2)['content_delivery']
        proof={'complete':True,'pass':True,'expected_checks':['Q1'],'assessments':{'Q1':{'status':'satisfied'}},'controls':{'passed':True}}
        delivery['independent_editor']={'status':'PASS','issues':[],'substance_review':proof,
            'independent_reader':{'model':'peer:cloud','independent_from':'peer:local','status':'PASS','substance_review':proof}}
        self.assertFalse(can_close_task({'strategy','calendar'},delivery))
        delivery['independent_editor']['independent_reader']['model']='another:cloud';self.assertTrue(can_close_task({'strategy','calendar'},delivery))
        delivery['independent_editor']['independent_reader']['model']='';self.assertFalse(can_close_task({'strategy','calendar'},delivery))


if __name__=='__main__':unittest.main()
