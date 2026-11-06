"""Shared dimension recovery and source/plan roles across arbitrary briefs.

These injected replies verify contracts, not live semantic accuracy.
"""
import copy
import io
import json
import random
import unittest
from unittest.mock import patch

import generic_text_review as review
from assertion_scope import classify_obvious_non_business
from claim_support import _quote_options, _source_quote_authority, adjudicate_factual_findings, public_copy_findings
from evidence_context import build_evidence_context
from independent_acceptance_editor import _inventory_segments, _inventory_business_assertions
from test_r62_review_boundaries import with_changes


class AllDimensionContracts(unittest.TestCase):
    def test_each_dimension_corrects_invalid_evidence_without_changing_content(self):
        draft='A meaningful complete sentence.'
        for dimension in ('requirements','completeness','format_and_tone'):
            for model in ('reviewer:cloud','local-reviewer:8b'):
                calls=[];events=[]
                def wire(req,timeout):
                    body=json.loads(req.data);calls.append(body)
                    user=json.loads(next(m['content'] for m in body['messages'] if m['role']=='user'))
                    self.assertEqual(user['draft_to_review'],draft)
                    self.assertEqual(user['original_request'],'Write one complete post.')
                    self.assertEqual(user['review_dimension'],dimension)
                    item={'evidence_id':user['review_evidence'][0]['evidence_id'],'reason':'An explained concrete defect.','severity':'major'}
                    if user['requirement_bindings']:
                        item.update(requirement_id=user['requirement_bindings'][0]['id'],
                                    failure_type='style_mismatch' if dimension=='format_and_tone' else 'other_explicit_requirement')
                    value={'pass':False,'issues':[item]}
                    if len(calls)==1:item['evidence_id']='NOT_ISSUED'
                    if body.get('tools'):
                        message={'tool_calls':[{'function':{'name':'return_structured_result','arguments':value}}]}
                    else:message={'content':json.dumps(value)}
                    return io.BytesIO(json.dumps({'message':message}).encode())
                with patch('ollama_resilience.urllib.request.urlopen',side_effect=wire):
                    result=review.review_dimension('Write one complete post.',draft,{},dimension,model,
                        report=lambda stage,**kw:events.append((stage,kw)))
                self.assertEqual(len(calls),2,(dimension,model))
                self.assertFalse(result['pass']);self.assertEqual(result['issues'][0]['quote'],draft)
                self.assertTrue(any(s=='review_contract_invalid' for s,_ in events))

    def test_missing_or_wrong_criterion_fields_remain_unavailable_after_one_correction(self):
        for malformed in ({'pass':True,'issues':None},{'pass':'true','issues':[]},
                          {'pass':False,'issues':[]},{'pass':True,'issues':[],'extra':True}):
            with patch('generic_text_review.request_chat',return_value={'message':{'content':json.dumps(malformed)}}) as call:
                with self.assertRaises(ValueError):
                    review.review_dimension('Write.','Complete text.',{},'completeness','reviewer')
            self.assertEqual(call.call_count,2)

    def test_transport_failure_does_not_enter_a_new_semantic_attempt(self):
        with patch('generic_text_review.request_chat',side_effect=TimeoutError('Offline')) as call:
            with self.assertRaises(TimeoutError):review.review_dimension('Write.','Text.',{},'completeness','reviewer')
        self.assertEqual(call.call_count,1)

    def test_soundness_is_checked_without_requiring_business_evidence_for_general_information(self):
        draft='This single step guarantees that nothing can ever go wrong.'
        def chat(body,**kw):
            user=json.loads(body['messages'][1]['content'])
            self.assertIn('не делает истинной',body['messages'][0]['content'])
            self.assertIn('абсолютную гарантию',body['messages'][0]['content'])
            return {'message':{'content':json.dumps({'pass':False,'issues':[
                {'evidence_id':user['review_evidence'][0]['evidence_id'],'reason':'The absolute guarantee does not follow from the described step.','severity':'major',
                 'requirement_id':user['requirement_bindings'][0]['id'],
                 'failure_type':'required_content_missing'}]})}}
        with patch('generic_text_review.request_chat',side_effect=chat) as call:
            out=review.review_dimension('Write useful guidance.',draft,{},'completeness','reviewer')
        self.assertFalse(out['pass']);self.assertEqual(call.call_count,1)


class SharedEditorialScope(unittest.TestCase):
    def test_source_adjudication_cannot_reintroduce_a_planning_exemption_rejected_by_inventory(self):
        for text in ('Подготовка контента уже осуществлена.',
                     'Использование публикаций уже осуществлено.',
                     'Публикация гарантии результата для каждого клиента.'):
            calls=[]
            def ask(*a,**kw):calls.append(1);return {'decisions':{'F001':with_changes()}}
            item={'document':'strategy','field_type':'document_narrative',
                  'field_role':'План действий','section_path':['Strategy','План действий'],
                  'quote':text,'evidence_segment':text}
            out=adjudicate_factual_findings('Prepare a strategy.',[item],ask=ask,require_semantic_contract=True)
            self.assertEqual(calls,[1],text)
            self.assertEqual(out['blocking'][0]['adjudication'],'unsupported')
            self.assertFalse(out['non_business'])

    def test_nominal_content_operations_keep_exact_quotes_but_are_not_existing_business_processes(self):
        rng=random.Random(6301)
        for index in range(35):
            token=''.join(rng.choice('abxyz') for _ in range(12))
            steps=[f'Использование {token} для публикации {index+1} постов.',
                   f'{index+2}. Публикация контента в согласованный день.',
                   f'{index+3}. Мониторинг метрик после публикации.',
                   f'{index+4}. Monitoring content metrics for {token}.']
            body='# Strategy\n\n## Implementation steps\n'+'\n'.join(steps)
            rows=_inventory_segments(body,'')
            for row in rows:
                issue={**row,'quote':row['content'],'evidence_segment':row['content']}
                scope=classify_obvious_non_business(issue)
                self.assertEqual(scope and scope['statement_class'],'planning_constraint',(row,scope))
                self.assertIn(row['content'],body)
            with patch('independent_acceptance_editor.request_chat',side_effect=AssertionError('No model is needed for these explicit editorial actions')):
                out=_inventory_business_assertions('Make a content plan.',body,'')
            self.assertEqual(out,[])

    def test_editorial_heading_does_not_exempt_existing_fulfillment_or_published_claims(self):
        cases=['Мы гарантируем результат после публикации.',
               'Мониторинг метрик уже проводится нашей командой.',
               'Публикация контента уже осуществлена.',
               'Публикация поста: мы бесплатно доставим каждый заказ.',
               'Публикация гарантии результата для каждого клиента.']
        for text in cases:
            item={'document':'strategy','field_type':'document_narrative',
                'section_path':['Strategy','Implementation steps'],'quote':text}
            self.assertIsNone(classify_obvious_non_business(item),text)

    def test_publication_copy_does_not_inherit_a_strategy_planning_exemption(self):
        item={'document':'calendar','field_type':'caption','section_path':['Strategy'],
              'quote':'Мониторинг метрик после публикации.'}
        scope=classify_obvious_non_business(item)
        self.assertNotEqual(scope and scope['statement_class'],'planning_constraint')


class SourceContextContracts(unittest.TestCase):
    def test_explicit_named_entity_context_inside_task_sentence_remains_citable(self):
        rng=random.Random(6302)
        for index in range(30):
            name=''.join(rng.choice('abcdefghijkl') for _ in range(15))
            category='category'+str(index)
            for request in (f'Составь текст для проекта «{name}», который работает как {category}.',
                            f'Write a post for “{name}”, which is a {category}.'):
                claim=f'Наш проект «{name}» относится к {category}.'
                options=_quote_options(build_evidence_context(request).sources,{'quote':claim},authoritative_only=True)
                self.assertTrue(any(row['quote']==request for row in options.values()),request)
                def ask(model,system,user,**kw):
                    data=json.loads(user);option=data['authorized_quote_options'][0]
                    return {'decisions':{'F001':with_changes(source_relation='entails',verdict='supported',
                        source_quote_id=option['quote_id'],reason='The supplied entity description establishes this category.')}}
                out=adjudicate_factual_findings(request,public_copy_findings(claim),ask=ask,require_semantic_contract=True)
                self.assertEqual(out['blocking'],[],out);self.assertEqual(len(out['supported']),1)

    def test_invented_hypothetical_or_unprovided_context_is_not_authority(self):
        for request in ('Придумай текст о проекте «A», который работает как B.',
                        'Напиши о вымышленном проекте «A», который является B.',
                        'Imagine a company “A”, which is a B.',
                        'Напиши, что наш проект оказывает новую услугу.',
                        'Составь текст для проекта «A», который работает как B. Не выдумывай услугу.'):
            self.assertFalse(_source_quote_authority(request)[0],request)

    def test_relation_without_its_source_operand_is_repaired_once_not_sent_to_author(self):
        request='Подтверждённая услуга: проверка файлов по согласованию.'
        calls=[]
        def ask(model,system,user,**kw):
            data=json.loads(user);calls.append(data)
            if len(calls)==1:
                return {'decisions':{'F001':with_changes(speech_act='invitation',assertion_subject='business',
                    invitation_kind='service_access',source_relation='instruction')}}
            self.assertIn('source_quote_id=none',system)
            option=next(row for row in data['authorized_quote_options'] if 'Подтверждённая услуга' in row['quote'])
            return {'decisions':{'F001':with_changes(speech_act='invitation',assertion_subject='reader',
                invitation_kind='service_access',source_relation='entails',source_quote_id=option['quote_id'],verdict='supported')}}
        out=adjudicate_factual_findings(request,public_copy_findings('Запишитесь на проверку файлов по согласованию.'),
            ask=ask,require_semantic_contract=True)
        self.assertEqual(len(calls),2);self.assertFalse(out['blocking']);self.assertEqual(len(out['supported']),1)

    def test_missing_source_operand_cannot_become_a_completed_unsupported_decision(self):
        for relation in ('entails','contradicts','instruction'):
            calls=[]
            def ask(*a,**kw):calls.append(1);return {'decisions':{'F001':with_changes(source_relation=relation)}}
            out=adjudicate_factual_findings('Prepare a text.',public_copy_findings('One factual statement.'),
                ask=ask,require_semantic_contract=True)
            self.assertEqual(len(calls),2);self.assertEqual(out['blocking'][0]['adjudication'],'unavailable')


if __name__=='__main__':unittest.main()
