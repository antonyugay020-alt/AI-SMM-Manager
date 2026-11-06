"""Required semantic work, reviewer controls, revision provenance and no false closure."""
import copy
import io
import os
import json
from pathlib import Path
import sqlite3
import tempfile
import random
import unittest
from unittest.mock import Mock,patch

from artifact_review import (SubstanceReviewContract,SubstanceControlFailure,
    ReviewContractError,document_substance_contract)
import independent_acceptance_editor as editor
from independent_quality_gate import final_quality_gate
from substance_fixture import assessed_reply
from test_r81_typed_windows import documents
from task_artifacts import (original_document_request,revision_seed,store_pending_task,
    ensure_pending_revision_column,load_revision_seed)
from stage57_core import receipt


class SubstanceContractTests(unittest.TestCase):
    def contract(self):
        return SubstanceReviewContract.create('Explain a cause.',{'text':'A defined cause explains the effect.'},
            [{'id':'example','criteria':{'topic_delivery':['text'],'reader_value':['text']}}])

    def values(self,contract):
        return {key:{'status':'satisfied','evidence_id':check['evidence_ids'][0],
                     'reason':'A concrete explanation follows the specified source relationship.'}
                for key,check in contract.checks.items()}

    def controls(self,contract):return {key:row['expected'] for key,row in contract.controls.items()}

    def test_complete_records_retain_exact_source_text_and_policy(self):
        c=self.contract();out=c.validate(self.values(c),[],self.controls(c))
        self.assertTrue(out['pass']);self.assertEqual(out['policy'],'substantive_delivery_r82')
        self.assertEqual({r['quote'] for r in out['assessments'].values()},set(c.catalog.values()))

    def test_empty_partial_or_extra_assessments_cannot_approve(self):
        c=self.contract()
        for value in (None,{},dict(list(self.values(c).items())[:1]),{**self.values(c),'invented':{}}):
            with self.assertRaises(ReviewContractError):c.validate(value,[],self.controls(c))

    def test_unknown_or_foreign_locator_blocks_instead_of_authorizing_an_edit(self):
        c=self.contract();key=next(iter(c.checks))
        for field,value in (('status','unknown'),('evidence_id','different-document')):
            verdict=self.values(c);verdict[key][field]=value
            with self.assertRaises(ReviewContractError):c.validate(verdict,[],self.controls(c))

    def test_failed_control_is_not_a_retryable_contract_correction(self):
        c=self.contract();answers=self.controls(c);key=next(iter(answers));answers[key]=not answers[key]
        with self.assertRaises(SubstanceControlFailure) as caught:c.validate(self.values(c),[],answers)
        self.assertFalse(caught.exception.retryable)

    def test_controls_are_fresh_varied_and_gold_is_not_sent_to_the_model(self):
        identities=set();tasks=set()
        for seed in range(40):
            with patch('artifact_review.secrets.SystemRandom',return_value=random.Random(seed)):c=self.contract()
            identities.update(c.controls)
            tasks.update(row['task'] for row in c.controls.values())
            self.assertEqual(sorted(row['expected'] for row in c.controls.values()),[False,True])
            self.assertTrue(all('expected' not in row for row in c.control_payload().values()))
        self.assertEqual(len(identities),80);self.assertGreaterEqual(len(tasks),3)

    def test_a_defect_requires_a_concrete_finding_and_overrides_positive_status(self):
        c=self.contract();key=next(iter(c.checks));values=self.values(c)
        values[key]['status']='defect'
        with self.assertRaises(ReviewContractError):c.validate(values,[],self.controls(c))
        values[key]['status']='satisfied'
        issue={'type':'quality','criterion_id':key,'evidence_id':'text'}
        self.assertFalse(c.validate(values,[issue],self.controls(c))['pass'])

    def test_a_quality_finding_cannot_escape_its_criterion(self):
        c=self.contract()
        for issue in ({'type':'quality','criterion_id':'none','evidence_id':'text'},
                      {'type':'quality','criterion_id':next(iter(c.checks)),'evidence_id':'foreign'}):
            with self.assertRaises(ReviewContractError):c.validate(self.values(c),[issue],self.controls(c))

    def test_portfolio_criteria_cover_captions_and_exclude_kpi_repetition(self):
        st,ca=documents(rows=3);rows=editor._inventory_segments(st,ca)
        c=document_substance_contract('Create strategy and calendar.',rows)
        check=next(row for row in c.checks.values() if row['criterion']=='portfolio_distinction')
        by_id={row['evidence_id']:row for row in rows}
        self.assertEqual({by_id[ref]['field_type'] for ref in check['evidence_ids']},{'caption'})
        self.assertEqual(len({by_id[ref]['row_context']['slot'] for ref in check['evidence_ids']}),3)

    def test_bare_pass_from_a_model_cannot_become_native_document_approval(self):
        with patch.object(editor,'request_chat',return_value={'message':{'content':'{"pass":true,"issues":[]}'}}):
            out=editor.call_editor('Prepare a strategy and calendar.','A strategic explanation.','A useful draft.')
        self.assertFalse(out['pass']);self.assertEqual(out['review_state'],'unavailable')

    def test_native_gateway_requires_all_work_records_and_source_locators(self):
        st,ca=documents(rows=3)
        def chat(payload,**kwargs):
            return {'message':{'content':json.dumps(assessed_reply(payload,{'pass':True,'issues':[]}))}}
        with patch.object(editor,'request_chat',side_effect=chat):
            out=editor.call_editor('Create strategy and calendar.',st,ca)
        proof=out['substance_review'];self.assertTrue(out['pass']);self.assertTrue(proof['complete'])
        self.assertEqual(set(proof['assessments']),set(proof['expected_checks']))
        self.assertTrue(any(r['criterion']=='portfolio_distinction' for r in proof['assessments'].values()))

    def test_openai_adapter_transmits_complete_substantive_contract_and_rejects_missing_work(self):
        seen=[]
        def wire(request,timeout):
            self.assertEqual(request.full_url,'https://api.openai.com/v1/chat/completions')
            body=json.loads(request.data);seen.append(body)
            self.assertEqual(body['response_format'],{'type':'json_object'})
            content=body['messages'][0]['content']
            schema=json.loads(content.split('JSON Schema ответа: ')[-1])
            self.assertIn('assessments',schema['required'])
            self.assertIn('quality_controls',schema['required'])
            answer=assessed_reply({'format':schema,'messages':body['messages']},{'pass':True,'issues':[]})
            if missing:answer.pop('quality_controls')
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(answer)}}]}).encode())
        missing=False
        with patch.dict(os.environ,{'OPENAI_API_KEY':'mock-only','AI_SMM_ENABLE_OPENAI_API':'1'}), \
             patch('urllib.request.urlopen',side_effect=wire):
            result=editor.call_substance_reader('Prepare a useful strategy and calendar.',
                'A strategic explanation.','An educational explanation.',model='openai/gpt-6-luna')
            self.assertTrue(result['pass'],result)
            self.assertTrue(result['substance_review']['controls']['passed'])
            missing=True
            result=editor.call_substance_reader('Prepare a useful strategy and calendar.',
                'A strategic explanation.','An educational explanation.',model='openai/gpt-6-luna')
        self.assertFalse(result['pass']);self.assertTrue(seen)

    def test_a_focused_topic_defect_retains_caption_quote_row_and_criterion(self):
        st,ca=documents(rows=2);target=None
        def chat(payload,**kwargs):
            nonlocal target
            content=payload['messages'][1]['content'];manifest=json.loads(content.split('QUALITY_CRITERIA_JSON:\n')[-1])
            check=next(((key,row) for key,row in manifest['checks'].items()
                        if row['criterion']=='topic_delivery'),None)
            answer={'pass':True,'issues':[]}
            if check and target is None:
                key,row=check;ref=next(r for r in row['evidence_ids'] if r.startswith('calendar:'))
                target=ref;answer={'pass':False,'issues':[{'document':'calendar','type':'quality',
                    'evidence_id':ref,'criterion_id':key,
                    'reason':'The promised explanation is replaced by a direction to another source.'}]}
            return {'message':{'content':json.dumps(assessed_reply(payload,answer))}}
        with patch.object(editor,'request_chat',side_effect=chat):out=editor.call_editor('Explain the planned topics.',st,ca)
        self.assertFalse(out['pass'])
        issue=next(x for x in out['issues'] if x.get('criterion_id'))
        self.assertEqual(issue['evidence_id'],target);self.assertIn(issue['quote'],ca)
        self.assertTrue(issue['row_context']['slot'])

    def test_transport_failure_defers_remaining_windows_and_inventory_without_repeated_calls(self):
        st,ca=documents(rows=16)
        with patch.object(editor,'request_chat',side_effect=TimeoutError('Unavailable')) as chat, \
             patch.object(editor,'_inventory_business_assertions') as inventory:
            out=editor.call_editor('Create strategy and calendar.',st,ca,require_inventory=True)
        self.assertEqual(chat.call_count,1);inventory.assert_not_called()
        self.assertTrue(out['review_transport_unavailable']);self.assertFalse(out['pass'])

    def test_wrong_control_stops_same_model_and_cannot_trigger_draft_repair(self):
        st,ca=documents(rows=16)
        def chat(payload,**kwargs):
            value=assessed_reply(payload,{'pass':True,'issues':[]})
            key=next(iter(value['quality_controls']));value['quality_controls'][key]=not value['quality_controls'][key]
            return {'message':{'content':json.dumps(value)}}
        with patch.object(editor,'request_chat',side_effect=chat) as gateway:
            out=editor.call_editor('Create strategy and calendar.',st,ca)
        self.assertEqual(gateway.call_count,1);self.assertTrue(out['review_control_unavailable'])
        self.assertEqual(out['substance_review']['pass'],False)

    def test_standalone_entry_requires_substance_proof_without_restricting_literal_code(self):
        from text_workflow import run_text_task
        seen=[]
        run_text_task('Explain the literal sequence \\n in a code sample.',{},
            planner=lambda *a,**k:{'action':'write','answer':'The literal \\n sequence is shown in code.'},
            quality_cycle=lambda request,result,context,**k:seen.append(context) or result)
        self.assertTrue(seen[0]['substance_review_required'])
        from generic_text_review import review_dimension
        def chat(payload,**kwargs):return {'message':{'content':json.dumps(assessed_reply(payload,{'pass':True,'issues':[]}))}}
        with patch('generic_text_review.request_chat',side_effect=chat):
            out=review_dimension('Explain the literal \\n sequence.','Code: `print("\\n")`.',
                {'substance_review_required':True},'completeness','independent')
        self.assertTrue(out['pass']);self.assertTrue(out['substance_review']['pass'])

    def test_native_production_requires_a_different_substantive_reader(self):
        import production_pipeline as pipeline
        for secondary_defect in (False,True):
            st,ca=documents(rows=2);wire_models=[]
            bundle={'action':'compound_documents','documents':[
                {'kind':'strategy','content':st,'valid':True},{'kind':'calendar','content':ca,'valid':True}],
                'content_delivery':{'unfulfilled':[],'rendered_media':'not_requested',
                    'deliverables':{'strategy':'validated','calendar':'validated'},
                    'scope':{'weeks':1,'per_week':2,'planned_total':2},'checked_posts':2,
                    'content_components_checked':dict.fromkeys(('caption','visual','cta','kpi','audience','measurement','materials'),2),
                    'plan_preflight':{'reviewer_available':True,'unresolved_slots':[]},'task_orchestration':{'completed':True}}}
            def chat(payload,**kwargs):
                wire_models.append(payload['model']);schema=payload['format']
                if 'assessments' in schema.get('properties',{}):
                    answer={'pass':True,'issues':[]}
                    manifest=json.loads(payload['messages'][1]['content'].split('QUALITY_CRITERIA_JSON:\n')[-1])
                    if secondary_defect and payload['model']=='reader_secondary':
                        key,row=next((key,row) for key,row in manifest['checks'].items() if row['criterion']=='topic_delivery')
                        answer={'pass':False,'issues':[{'document':'calendar','type':'quality','criterion_id':key,
                            'evidence_id':row['evidence_ids'][0],'reason':'The text repeats a general instruction without the promised usable explanation.'}]}
                    answer=assessed_reply(payload,answer)
                else:
                    data=json.loads(payload['messages'][1]['content'])
                    if 'review_evidence' in data:return {'message':{'content':'{"pass":true,"issues":[]}'}}
                    answer={'decisions':{row['id']:{'action':'correct_artifact','remedy_scope':'current',
                        'required_change':'Supply the promised explanation in the cited caption.',
                        'reason':'The absence of useful explanation is a concrete topic defect.'} for row in data['review_findings']}}
                return {'message':{'content':json.dumps(answer)}}
            with patch('compound_tasks.execute_compound',return_value=copy.deepcopy(bundle)), \
                 patch('generic_text_review.select_independent_model',side_effect=['reader_primary','reader_secondary']), \
                 patch('ollama_resilience.request_chat',side_effect=chat),patch('generic_text_review.request_chat',side_effect=chat), \
                 patch.object(editor,'_inventory_business_assertions',return_value=[]),patch.object(editor,'request_chat',side_effect=chat):
                out=pipeline.run_document_task('Create strategy and calendar.',{'randomized_acceptance_run':True,
                    'acceptance_execution_mode':'first_pass','acceptance_profile':'fast'},model='separate_author')
            report=out['content_delivery']['independent_editor'];self.assertIn('independent_reader',report,report);reading=report['independent_reader']
            self.assertEqual(reading['model'],'reader_secondary');self.assertEqual(reading['independent_from'],'reader_primary')
            self.assertEqual(reading['status'],'BLOCKED' if secondary_defect else 'PASS')
            self.assertEqual(report['status'],'BLOCKED' if secondary_defect else 'PASS')
            self.assertIn('reader_primary',wire_models);self.assertIn('reader_secondary',wire_models)


class RevisionProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE manager_documents(id TEXT,project_id TEXT,kind TEXT,title TEXT,content TEXT,created_at TEXT,updated_at TEXT)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT,question TEXT)')
        self.request='Разработай стратегию и контент-план: Instagram, 1 неделя, всего 1 публикация.'
        from publication_writer import week_to_markdown
        slot={'week':1,'day':'Вт','topic':'A useful topic','platform':'Instagram','format':'Карусель','goal':'Explain a choice.'}
        post={'id':'1/Вт','topic':slot['topic'],'caption':'Useful explanation.','visual':'A clear future diagram.',
              'cta':'Ask a question.','kpi':'Questions.','measurement':'Count the questions.','audience':'Readers.','materials':'Prepare the diagram.'}
        self.calendar=week_to_markdown([post],[slot])
        self.strategy='# Strategy\nA coherent strategic explanation.'
        for ident,kind,body in [('s','strategy',self.strategy),('c','calendar',self.calendar)]:
            self.db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?,?,?)',(ident,'p',kind,kind,body,'now','now'))
            receipt(self.db,'p',self.request,kind,body,'saved')

    def tearDown(self):self.db.close()

    def test_revision_is_bound_to_selected_saved_bytes_and_project(self):
        self.assertEqual(original_document_request(self.db,'p','c'),self.request)
        self.assertIsNone(original_document_request(self.db,'other','c'))
        seed=revision_seed(self.db,'p',self.request,'c')
        self.assertEqual(seed['strategy'],self.strategy);self.assertEqual(len(seed['posts']),1)
        self.assertNotIn('already_saved',str(seed['posts']))

    def test_manual_edit_cannot_borrow_an_older_execution_receipt(self):
        self.db.execute('UPDATE manager_documents SET content=? WHERE id=?',('A manual replacement.','c'))
        self.assertIsNone(original_document_request(self.db,'p','c'))
        with self.assertRaises(ValueError):revision_seed(self.db,'p',self.request,'c')

    def test_revision_metadata_migration_preserves_existing_pending_and_human_question(self):
        self.db.execute('INSERT INTO ai_pending_tasks VALUES(?,?,?)',('p',self.request,'Which option?'))
        ensure_pending_revision_column(self.db)
        self.assertEqual(self.db.execute('SELECT request,question,revision_id FROM ai_pending_tasks').fetchone(),
                         (self.request,'Which option?',None))
        rid='a'*32;store_pending_task(self.db,'p',self.request,'A human clarification.',rid)
        self.assertEqual(self.db.execute('SELECT question,revision_id FROM ai_pending_tasks').fetchone(),('A human clarification.',rid))
        ensure_pending_revision_column(self.db)

    def test_revision_input_cannot_cross_project_request_or_escape_workspace(self):
        with tempfile.TemporaryDirectory() as home:
            path=Path(home)/'drafts';path.mkdir();rid='b'*32
            (path/('revision_input_'+rid+'.json')).write_text(json.dumps({'project_id':'p','request':self.request}), encoding='utf-8')
            self.assertEqual(load_revision_seed(home,'p',self.request,rid)['request'],self.request)
            for pid,request,key in [('other',self.request,rid),('p','Another request',rid),('p',self.request,'../x')]:
                with self.assertRaises(ValueError):load_revision_seed(home,pid,request,key)

    def test_revision_checkpoints_are_isolated_and_seed_posts_are_not_approved(self):
        import compound_tasks as ct
        import hashlib
        seed=revision_seed(self.db,'p',self.request,'c');rid='d'*32
        class StopAfterSeed(Exception):pass
        with tempfile.TemporaryDirectory() as home:
            drafts=Path(home)/'drafts';drafts.mkdir()
            original=hashlib.sha256((home+'\n'+self.request+'\n1\n1').encode()).hexdigest()
            source=drafts/('publication_progress_'+original[:16]+'.json');source.write_bytes(b'Original saved checkpoint')
            with patch.object(ct,'extract_facts',side_effect=StopAfterSeed),self.assertRaises(StopAfterSeed):
                ct.execute_compound(self.request,{'project_workspace':home,'task_mode':'revision',
                    'revision_id':rid,'revision_seed':seed})
            self.assertEqual(source.read_bytes(),b'Original saved checkpoint')
            fresh=next(p for p in drafts.glob('publication_progress_*.json') if p!=source)
            self.assertEqual(json.loads(fresh.read_text(encoding='utf-8'))['posts'],{})
            repair=json.loads(next(drafts.glob('publication_repair_*.json')).read_text(encoding='utf-8'))['drafts']
            self.assertEqual(repair['1/Вт']['post'],seed['posts']['1/Вт'])
            self.assertEqual(repair['1/Вт']['review_state'],'unavailable')


if __name__=='__main__':unittest.main()
