"""Complete finding contracts across repair adapters; injected model replies."""
import copy
import json
import random
import tempfile
import unittest
from unittest.mock import patch

import compound_tasks as compound
import generic_text_review as review
from document_recovery import patch_document
from publication_writer import repair_fields,repair_single
from text_task_contract import TextRevisionContract
from test_stage69_scope import good_post


def finding(quote,target='strategy',change='Correct every affected section.'):
    return {'document':target,'quote':quote,'reason':'A confirmed requirement is not satisfied.',
        'evidence_id':'E0001_host','field_type':'document_narrative','check':'format_and_tone',
        'finding_validation':{'decision':'confirmed','action':'correct_artifact',
            'remedy_target':target,'required_change':change,'model':'independent-reader'},
        'review_scope':{'available_artifacts':[target],'pending_artifacts':['calendar'] if target=='strategy' else []},
        'adjudication_trace':{'source_relation':'unrelated','reason':'No authorized fact proves the assertion.'}}


class DocumentTransport(unittest.TestCase):
    def test_multiline_required_change_and_scope_survive_document_patch(self):
        rng=random.Random(7201)
        for index in range(20):
            token=''.join(rng.choice('абвxyz') for _ in range(14))
            body='# Стратегия\n- Wrong first section '+token+'\n- Wrong second section '+token+'\n- Preserve known content.\n'
            issue=finding('Wrong first section '+token,change='Correct both affected sections '+token)
            issue['future_contract_metadata']={'id':token,'items':[{'scope':'artifact'}]}
            original=copy.deepcopy(issue);seen=[]
            def ask(model,system,user,**kw):
                data=json.loads(user);seen.append(data)
                self.assertEqual(data['structured_findings'],[original])
                self.assertEqual(data['priority_line_ids'],['L00002'])
                return json.dumps({'changes':[{'line_id':'L00002','text':'- Исправленный первый раздел '+token},
                    {'line_id':'L00003','text':'- Исправленный второй раздел '+token}]})
            out=patch_document(ask,'writer','Correct both sections.',body,'A requirement failed.',findings=[issue],constrained=True)
            self.assertIn('Исправленный первый раздел',out);self.assertIn('Исправленный второй раздел',out)
            self.assertTrue(out.endswith('- Preserve known content.\n'))
            self.assertEqual(issue,original)
    def test_protected_structure_remains_protected_despite_full_metadata(self):
        issue=finding('Wrong text.')
        with self.assertRaises(ValueError):
            patch_document(lambda *a,**kw:json.dumps({'changes':[{'line_id':'L00001','text':'# Changed title'}]}),
                'writer','Correct text.','# Existing title\nWrong text.','Reason',findings=[issue],constrained=True)
    def test_malformed_and_noop_replies_still_fail_after_complete_transport(self):
        for reply in ({'changes':[]},{'changes':[{'line_id':'L00001','text':'Wrong text.'}]},
                      {'changes':[{'line_id':'FOREIGN','text':'Changed text.'}]}):
            with self.assertRaises(ValueError):patch_document(lambda *a,**kw:json.dumps(reply),
                'writer','Correct.','Wrong text.','Reason',findings=[finding('Wrong text.')],constrained=True)


class SourceTransport(unittest.TestCase):
    def test_source_decision_proof_survives_aggregate_revision_gateway_and_checkpoint(self):
        draft='Мы обеспечим конкретный результат каждому клиенту.'
        def source_reply(payload,**kw):
            value=json.loads(payload['messages'][-1]['content'])
            return {'message':{'content':json.dumps({'decisions':{row['finding_id']:{
                'statement_class':'service_outcome','speech_act':'assertion','assertion_subject':'business',
                'source_relation':'unrelated','verdict':'unsupported','source_quote_id':'none','invitation_kind':'none',
                'reason':'No authorized source establishes the promised outcome.'} for row in value['findings']}})}}
        with patch.object(review,'request_chat',side_effect=source_reply):
            result=review.review_dimension('Write a general explanation.',draft,{},'factual_grounding','reader')
        self.assertFalse(result['pass'])
        original=result['issues'][0]
        issues=review._validated_issues(result['issues'],draft,'factual_grounding')
        self.assertEqual(issues[0]['adjudication_trace'],original['adjudication_trace'])
        self.assertEqual(issues[0]['statement_class'],'service_outcome')
        contract=TextRevisionContract.create(draft,issues)
        self.assertEqual(contract.findings[0]['adjudication_trace'],original['adjudication_trace'])
        with tempfile.TemporaryDirectory() as home:
            def revise(payload,**kw):
                data=json.loads(payload['messages'][-1]['content'])
                self.assertEqual(data['revision_findings'][0]['adjudication_trace'],original['adjudication_trace'])
                self.assertEqual(data['revision_findings'][0]['evidence_id'],original['evidence_id'])
                return {'message':{'content':json.dumps({'revised_text':'Общее объяснение без обещанного результата компании.'})}}
            with patch.object(review,'request_chat',side_effect=revise):
                out=review.repair_text('Write a general explanation.',draft,issues,{'project_workspace':home},model='writer')
            self.assertIn('Общее объяснение',out)
        self.assertEqual(original['adjudication_trace']['source_relation'],'unrelated')
    def test_unavailable_source_does_not_become_authorized_repair(self):
        with patch.object(review,'request_chat',return_value={'message':{'content':'{}'}}):
            out=review.review_dimension('Write.','Мы обеспечим конкретный результат.',{},'factual_grounding','reader')
        self.assertFalse(out['pass']);self.assertTrue(out['review_error']);self.assertEqual(out['issues'],[])


class PublicationTransport(unittest.TestCase):
    def test_both_publication_writers_receive_complete_finding_proof(self):
        slot={'week':1,'day':'Вт','topic':'An arbitrary topic'};post=good_post(slot)
        issue=finding(post['cta'],'calendar','Use a concrete reader question.')
        issue['field_type']='cta';snapshot=copy.deepcopy(issue);seen=[]
        def ask(m,s,u,**kw):
            data=json.loads(u);seen.append(data)
            self.assertEqual(data['review_findings'],[snapshot])
            if 'revision_spans' in data:return json.dumps({'replacements':{row['id']:'A concrete reader question.' for row in data['revision_spans']}})
            return json.dumps({**post,'cta':'A concrete reader question.'})
        updated=repair_fields(ask,'writer','Write.',{},'',slot,post,[issue])
        repair_single(ask,'writer','Write.',{},'',slot,post,[issue])
        self.assertEqual(updated['caption'],post['caption']);self.assertEqual(len(seen),2)
        self.assertEqual(issue,snapshot)


class CompoundFallbackTransport(unittest.TestCase):
    def test_same_proof_reaches_line_patch_and_both_full_document_fallbacks(self):
        request='Сделай SMM-стратегию и контент-план для Telegram на одну неделю, 2 публикации в неделю, всего 2 публикации.'
        body='# Стратегия\nWrong first section.\nWrong second section.'
        issue=finding('Wrong first section.',change='Correct both affected sections of the current strategy.')
        for strict in (True,False):
            seen=[]
            def producer(*args,**kw):
                repaired=kw['repair'](body,'Wrong language in both sections.',1)
                self.assertIn('Исправленный документ',repaired)
                return repaired,False,'Retained failure for this isolated adapter check.'
            def ask(model,system,user,**kw):
                seen.append((user,kw))
                if kw.get('json_schema'):
                    data=json.loads(user);self.assertEqual(data['structured_findings'],[issue])
                    return '{"invalid_patch":true}'
                self.assertIn(json.dumps([issue],ensure_ascii=False),user)
                self.assertIn('Markdown',system)
                self.assertNotIn('только JSON',system)
                self.assertNotIn('JSON-схеме',system)
                return '# Стратегия\nИсправленный документ.'
            with tempfile.TemporaryDirectory() as home, \
                 patch.object(compound,'analyze',return_value={'channels':'Telegram','channels_basis':'explicit'}), \
                 patch.object(compound,'produce_checked_strategy',side_effect=producer), \
                 patch.object(compound,'_ask',side_effect=ask):
                compound.execute_compound(request,{'project_workspace':home,'strict_document_recovery':strict,
                    'final_review_issues':[issue]},model='writer')
            self.assertEqual(len(seen),2 if strict else 1)
