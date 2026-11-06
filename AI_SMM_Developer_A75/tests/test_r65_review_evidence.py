"""Revision-bound provenance across model transport, panel and author repair.

Injected model judgements are not live content-quality evidence.
"""
import copy
import io
import json
import random
import unittest
from unittest.mock import patch

from artifact_review import ArtifactReviewContract, ReviewContractError
import generic_text_review as review
from text_task_contract import TextRevisionContract


def response_for(body, *, evidence_index=0, passed=False):
    data=json.loads(body['messages'][-1]['content'])
    issue={'evidence_id':data['review_evidence'][evidence_index]['evidence_id'],
           'reason':'The selected fragment contains a concrete unresolved defect.', 'severity':'major'}
    if data['requirement_bindings']:
        issue.update(requirement_id=data['requirement_bindings'][0]['id'],
                     failure_type='style_mismatch' if data['review_dimension']=='format_and_tone' else 'other_explicit_requirement')
    return {'pass':passed,'issues':[] if passed else [issue]}


class RevisionBoundEvidence(unittest.TestCase):
    def test_varied_unicode_and_structures_keep_exact_host_quotes_for_every_criterion(self):
        rng=random.Random(6501)
        drafts=['  Вопрос?\n\nОтвет с «кавычками» и ё.  ',
                '# Header\n1. Version 3.14 is listed.\n2. Next item.',
                '同じ文。\nUn fragment différent!\nنص مختلف',
                'Repeated sentence. Repeated sentence.',
                '{"field":"literal \\n and emoji 🧭"}']
        drafts += [''.join(rng.choice('абвxyz012«»😀') for _ in range(70)) for _ in range(20)]
        for dimension in ('requirements','completeness','format_and_tone'):
            for draft in drafts:
                def chat(body,**kwargs):
                    data=json.loads(body['messages'][-1]['content'])
                    props=body['format']['properties']['issues']['items']['properties']
                    self.assertNotIn('quote',props)
                    self.assertEqual(props['evidence_id']['enum'],[r['evidence_id'] for r in data['review_evidence']])
                    return {'message':{'content':json.dumps(response_for(body))}}
                with patch.object(review,'request_chat',side_effect=chat) as call:
                    out=review.review_dimension('Review this exact requested result.',draft,{},dimension,'judge')
                self.assertEqual(call.call_count,1)
                self.assertFalse(out['pass'])
                self.assertEqual(out['issues'][0]['quote'],draft)
                self.assertTrue(out['issues'][0]['evidence_id'])

    def test_invalid_artifact_inputs_fail_before_any_model_call(self):
        for request,fields,roles in [(None,{'text':'A'},None),('T',{},None),
                                     ('T',{'':'A'},None),('T',{'text':[]},None),
                                     ('T',{'text':'A'},[]),('T',{'text':'A'},{'text':[]})]:
            with self.assertRaises(ValueError):ArtifactReviewContract.create(request,fields,roles)

    def test_fragment_selection_does_not_expand_to_the_entire_document(self):
        draft='First sentence. Second sentence.\nThird sentence.'
        def chat(body,**kw):
            data=json.loads(body['messages'][-1]['content'])
            index=next(i for i,row in enumerate(data['review_evidence']) if row['text']=='Second sentence.')
            return {'message':{'content':json.dumps(response_for(body,evidence_index=index))}}
        with patch.object(review,'request_chat',side_effect=chat):
            result=review.review_dimension('Write.',draft,{},'completeness','judge')
        self.assertEqual(result['issues'][0]['quote'],'Second sentence.')

    def test_stale_ids_reject_changed_draft_request_field_and_role(self):
        first=ArtifactReviewContract.create('Task A',{'text':'Same sentence.'},{'text':'caption'})
        ident=first.spans[0].ident
        variants=[('Task B',{'text':'Same sentence.'},{'text':'caption'}),
                  ('Task A',{'text':'Same sentence. New sentence.'},{'text':'caption'}),
                  ('Task A',{'other':'Same sentence.'},{'other':'caption'}),
                  ('Task A',{'text':'Same sentence.'},{'text':'visual'})]
        for args in variants:
            with self.assertRaises(ValueError):ArtifactReviewContract.create(*args).resolve_evidence(ident)
        again=ArtifactReviewContract.create('Task A',{'text':'Same sentence.'},{'text':'caption'})
        self.assertEqual(again.resolve_evidence(ident).text,'Same sentence.')

    def test_reordered_fields_cannot_resolve_an_id_to_the_wrong_field(self):
        one=ArtifactReviewContract.create('Task',{'a':'Alpha','b':'Beta'})
        two=ArtifactReviewContract.create('Task',{'b':'Beta','a':'Alpha'})
        with self.assertRaises(ValueError):two.resolve_evidence(one.spans[0].ident)

    def test_invalid_id_gets_one_correction_on_identical_input_and_keeps_negative(self):
        calls=[]
        def chat(body,**kw):
            calls.append(copy.deepcopy(body));answer=response_for(body)
            if len(calls)==1:answer['issues'][0]['evidence_id']='E_not_issued'
            return {'message':{'content':json.dumps(answer)}}
        with patch.object(review,'request_chat',side_effect=chat):
            result=review.review_dimension('Write a result.','Original text.',{},'requirements','judge')
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0]['messages'][-1],calls[1]['messages'][-1])
        self.assertEqual(calls[0]['format'],calls[1]['format'])
        self.assertFalse(result['pass'])
        self.assertEqual(result['issues'][0]['quote'],'Original text.')

    def test_malformed_evidence_reason_and_forged_quotes_never_become_approval(self):
        mutations=[('evidence_id',None),('evidence_id',[]),('evidence_id','E_foreign'),
                   ('reason',''),('reason','   '),('reason','x'*501),('reason',False),
                   ('severity','minor'),('quote','Invented quote')]
        for key,value in mutations:
            def chat(body,**kw):
                answer=response_for(body);answer['issues'][0][key]=value
                return {'message':{'content':json.dumps(answer)}}
            with patch.object(review,'request_chat',side_effect=chat) as call:
                with self.assertRaises(ReviewContractError):
                    review.review_dimension('Write a result.','Original text.',{},'completeness','judge')
            self.assertEqual(call.call_count,2,(key,value))

    def test_missing_requirement_content_can_target_whole_artifact_without_invented_quote(self):
        def chat(body,**kw):return {'message':{'content':json.dumps(response_for(body))}}
        with patch.object(review,'request_chat',side_effect=chat):
            result=review.review_dimension('Include an explicit conclusion.','Introduction only.',{},'requirements','judge')
        issue=result['issues'][0]
        self.assertEqual(issue['quote'],'Introduction only.')
        self.assertEqual(issue['requirement_quote'],'Include an explicit conclusion.')

    def test_completed_finding_ids_survive_aggregate_partial_failure_and_revision_contract(self):
        assignments=dict(zip(review.CHECKS,['a','b','c','d']))
        def chat(body,**kw):
            data=json.loads(body['messages'][-1]['content'])
            if data['review_dimension']=='completeness':
                return {'message':{'content':json.dumps(response_for(body))}}
            if data['review_dimension']=='format_and_tone':raise TimeoutError('Unavailable style editor')
            return {'message':{'content':'{"pass":true,"issues":[]}'}}
        with patch.object(review,'request_chat',side_effect=chat), \
             patch.object(review,'review_public_copy_sources',return_value={'pass':True,'issues':[]}):
            result=review.review_text('Write.','Exact rejected draft.',{},model='writer',reviewer_models=assignments)
        self.assertEqual(result['review_state'],'partial')
        self.assertEqual(set(result['review_errors']),{'format_and_tone'})
        issue=result['issues'][0]
        self.assertEqual(issue['check'],'completeness')
        self.assertTrue(issue['review_signature'])
        revision=TextRevisionContract.create('Exact rejected draft.',result['issues'])
        self.assertEqual(revision.findings[0]['evidence_id'],issue['evidence_id'])
        self.assertEqual(revision.findings[0]['review_signature'],issue['review_signature'])

    def test_native_and_cloud_transport_share_the_same_id_schema_and_negative_result(self):
        for model in ('local:8b','reviewer:cloud'):
            calls=[]
            def wire(req,timeout):
                body=json.loads(req.data);calls.append(body)
                schema=body['tools'][0]['function']['parameters'] if body.get('tools') else body['format']
                self.assertIn('evidence_id',schema['properties']['issues']['items']['required'])
                answer=response_for(body)
                if body.get('tools'):
                    message={'tool_calls':[{'function':{'name':'return_structured_result','arguments':answer}}]}
                else:message={'content':json.dumps(answer)}
                return io.BytesIO(json.dumps({'message':message}).encode())
            with patch('ollama_resilience.urllib.request.urlopen',side_effect=wire):
                result=review.review_dimension('Write.','Original content.',{},'requirements',model)
            self.assertEqual(len(calls),1)
            self.assertFalse(result['pass'])
            self.assertEqual(result['issues'][0]['quote'],'Original content.')

    def test_publication_contract_rejects_extra_revised_text_even_when_null(self):
        contract=ArtifactReviewContract.create('Write.',{'caption':'Immutable copy.'})
        with self.assertRaises(ValueError):contract.validate({'approved':True,'issues':[],'revised_post':None})


if __name__=='__main__':unittest.main()
