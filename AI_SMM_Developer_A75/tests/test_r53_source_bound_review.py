"""Cloud response codecs and request-bound editorial findings, across domains."""
import copy
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks as ct
import generic_text_review as review
from deliverable_contract import review_requirement_bindings, bind_requirement_findings
from ollama_resilience import request_chat, StructuredResponseError


class StructuredCloudResponseTests(unittest.TestCase):
    def call(self, response, payload=None):
        seen=[]; events=[]
        def transport(req,timeout):
            seen.append(json.loads(req.data))
            return io.BytesIO(json.dumps(response,ensure_ascii=False).encode('utf-8'))
        payload=payload or {'model':'arbitrary-model:cloud','format':{'type':'object'},
                            'messages':[{'role':'user','content':'Return a record'}]}
        saved=copy.deepcopy(payload)
        with patch('ollama_resilience.urllib.request.urlopen',side_effect=transport):
            result=request_chat(payload,stage='structured result',attempts=2,
                                report=lambda name,**kw:events.append((name,kw)))
        self.assertEqual(payload,saved)
        self.assertEqual(len(seen),1)
        return result,seen[0],events

    def test_typed_tool_arguments_preserve_arbitrary_text_without_json_string_repair(self):
        rng=random.Random(53103)
        text='Кавычки "внутри", новая строка\nи скобки { [ ] } — 🐾 '
        text+=''.join(rng.choice('абвгде\\"\n{}[]:;') for _ in range(150))
        value={'posts':[{'caption':text,'visual':{'scenes':[{'description':text}]}}]}
        result,sent,events=self.call({'message':{'content':'', 'tool_calls':[
            {'function':{'name':'return_structured_result','arguments':value}}]}})
        self.assertEqual(json.loads(result['message']['content']),value)
        self.assertNotIn('format',sent)
        self.assertEqual(len(sent['tools']),1)
        self.assertEqual(sent['tools'][0]['function']['parameters'],{'type':'object'})
        self.assertEqual(events[0][1]['response_contract_mode'],'tool_arguments')
        self.assertNotIn(text,repr(events))

    def test_string_arguments_are_decoded_strictly(self):
        value={'value':'Один "текст"\nв две строки'}
        args=json.dumps(value,ensure_ascii=False)
        result,_,_=self.call({'message':{'tool_calls':[
            {'function':{'name':'return_structured_result','arguments':args}}]}})
        self.assertEqual(json.loads(result['message']['content']),value)

    def test_unsupported_tool_names_or_ambiguous_calls_are_never_executed_or_retried(self):
        cases=([
            {'function':{'name':'delete_files','arguments':{'path':'anything'}}}],
            [{'function':{'name':'return_structured_result','arguments':{}}}],
            [{'function':{'name':'return_structured_result','arguments':{'result':{}}}}]*2)
        for calls in cases:
            with self.subTest(calls=calls), patch('ollama_resilience.urllib.request.urlopen',
                    return_value=io.BytesIO(json.dumps({'message':{'tool_calls':calls}}).encode())) as transport:
                with self.assertRaises(StructuredResponseError) as caught:
                    request_chat({'model':'arbitrary:cloud','format':{'type':'object','additionalProperties':False,'required':['value'],
                        'properties':{'value':{'type':'string'}}},'messages':[]},
                                 stage='no execution',attempts=2)
                self.assertEqual(transport.call_count,1)
                self.assertEqual(json.loads(caught.exception.response_text),calls)

    def test_legacy_json_text_remains_accepted_without_content_changes(self):
        examples=((json.dumps({'value':'Кавычки "внутри"'},ensure_ascii=False),{'value':'Кавычки "внутри"'}),
                  ('```json\n{"value":42}\n```',{'value':42}))
        for raw,expected in examples:
            with self.subTest(raw=raw):
                result,_,events=self.call({'message':{'content':raw}})
                self.assertEqual(json.loads(result['message']['content']),expected)
                self.assertEqual(events[0][1]['response_contract_mode'],'prompt_schema')

    def test_malformed_text_is_preserved_and_not_rewritten_or_retried(self):
        raw='{"caption":"PRIVATE_RESULT "unescaped" text"}'
        events=[]
        with patch('ollama_resilience.urllib.request.urlopen',
                   return_value=io.BytesIO(json.dumps({'message':{'content':raw}}).encode())) as transport:
            with self.assertRaises(StructuredResponseError) as caught:
                request_chat({'model':'arbitrary:cloud','format':'json','messages':[]},
                             stage='raw preservation',attempts=2,
                             report=lambda name,**kw:events.append((name,kw)))
        self.assertEqual(transport.call_count,1)
        self.assertEqual(caught.exception.response_text,raw)
        self.assertNotIn('PRIVATE_RESULT',repr(events))

    def test_failed_publication_response_is_saved_for_offline_replay(self):
        request='Создай SMM-стратегию и контент-план для Telegram на 1 неделю: 1 публикация в неделю, всего 1 публикация.'
        slot={'week':1,'day':'Ср','platform':'Telegram','format':'Текст с изображением',
              'topic':'Что уточнить до выбора','brief':'Вопрос аудитории','goal':'Получить вопросы','audience':'Читатели'}
        raw='{"caption":"PRIVATE_RESULT "unescaped" text"}'
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(ct,'analyze',return_value={'channels':'Telegram'}), \
                 patch.object(ct,'_ask',return_value='# Стратегия\n'+('Каналы. Цели. KPI. '*70)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',return_value=[slot]), \
                 patch.object(ct,'govern_plan',return_value=([slot],{},True)), \
                 patch.object(ct,'write_week',side_effect=StructuredResponseError('writer','malformed JSON',raw)) as writer:
                result=ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':folder,'randomized_acceptance_run':True})})
            files=list(Path(folder).rglob('model_response_rejected_*.json'))
            self.assertEqual(len(files),1)
            self.assertEqual(json.loads(files[0].read_text(encoding='utf-8'))['response'],raw)
            self.assertEqual(writer.call_count,1)
            self.assertFalse(result['documents'][-1]['valid'])


class SourceBoundRequirementTests(unittest.TestCase):
    def verdict_for(self, request, failure='verbatim_missing', *, mode='audience_fit'):
        draft='Готовый текст с полезной информацией и вопросом читателю.'
        def chat(payload,**kw):
            user=json.loads(payload['messages'][1]['content'])
            binding=next(b for b in user['requirement_bindings'] if (b['literal_directive'] if mode=='literal' else b['mode']==mode))
            obj={'pass':False,'issues':[{'evidence_id':user['review_evidence'][0]['evidence_id'],'reason':'Проверяемая причина',
                'severity':'major','requirement_id':binding['id'],'failure_type':failure}]}
            return {'message':{'content':json.dumps(obj,ensure_ascii=False)}}
        with patch('generic_text_review.request_chat',side_effect=chat):
            return review.review_dimension(request,draft,{},'requirements','reviewer')

    def test_literal_mention_is_not_inferred_from_context_labels(self):
        rng=random.Random(53107)
        for label in ('Аудитория','Целевая аудитория','Audience','Target audience'):
            audience=''.join(rng.choice('абвгдежзиклмнопрсту') for _ in range(20))
            request=f'Напиши пост. {label}: {audience}. Цель поста: получить вопросы.'
            with self.subTest(label=label):
                result=self.verdict_for(request)
                self.assertTrue(result['pass'])
                self.assertFalse(result['model_pass'])
                self.assertEqual(result['issues'],[])
                self.assertEqual(result['dismissed_issues'][0]['requirement_mode'],'audience_fit')
                self.assertIn(audience,result['dismissed_issues'][0]['requirement_quote'])

    def test_genuine_audience_and_objective_mismatch_remain_blocking(self):
        request='Напиши пост. Аудитория: новые читатели. Цель поста: получить вопросы.'
        for failure,mode in (('audience_mismatch','audience_fit'),('objective_mismatch','objective_fit')):
            with self.subTest(failure=failure):
                result=self.verdict_for(request,failure,mode=mode)
                self.assertFalse(result['pass'])
                self.assertEqual(result['issues'][0]['failure_type'],failure)
                self.assertEqual(result['dismissed_issues'],[])

    def test_explicit_mention_request_still_blocks_missing_text(self):
        for directive in ('Дословно включи фразу «Жители района».',
                          'Упомяни жителей района.', 'Mention the local audience explicitly.'):
            request='Напиши пост. Аудитория: жители района. '+directive
            result=self.verdict_for(request,mode='literal')
            self.assertFalse(result['pass'])
            self.assertFalse(result['dismissed_issues'])
        bindings=review_requirement_bindings('Аудитория: обязательно упомяни «жители района».')
        self.assertEqual(bindings[0]['mode'],'instruction')

    def test_unbound_or_cross_request_findings_fail_closed(self):
        first=review_requirement_bindings('Аудитория: одна группа.')
        second=review_requirement_bindings('Аудитория: другая группа.')
        issue={'requirement_id':first[0]['id'],'failure_type':'verbatim_missing'}
        with self.assertRaises(ValueError): bind_requirement_findings([issue],second)
        with self.assertRaises(ValueError): bind_requirement_findings([{}],first)
        with patch('generic_text_review.request_chat') as model:
            with self.assertRaises(ValueError):
                review.review_dimension('', 'Draft', {}, 'requirements', 'reviewer')
            model.assert_not_called()

    def test_present_but_invalid_calendar_cannot_count_as_complete_acceptance(self):
        import independent_acceptance_editor as editor
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'case_synthetic_documents.json'
            p.write_text(json.dumps({'request':'Стратегия и календарь на неделю.',
                'response':{'documents':[{'kind':'strategy','content':'Цели и задачи.','valid':True},
                                         {'kind':'calendar','content':'Календарь не завершён.','valid':False}]}}),encoding='utf-8')
            with patch.object(editor,'call_editor',return_value={'pass':True,'issues':[]}):
                result=editor.review_saved_results(Path(folder))
        case=result['cases'][0]
        self.assertEqual(case['status'],'FAIL')
        self.assertFalse(case['document_coverage']['complete'])
        self.assertEqual(case['document_coverage']['invalid'],['calendar'])

    def test_context_adjudication_survives_the_full_four_model_quality_cycle(self):
        request='Напиши один пост. Аудитория: постоянные читатели.'
        draft='Текст с полезной информацией и вопросом.'
        assignment=dict(zip(review.CHECKS,['first','second','third','fourth']))
        def chat(payload,**kw):
            user=json.loads(payload['messages'][1]['content'])
            if 'findings' in user:
                from source_review_fixture import general_source_reply
                return general_source_reply(payload)
            if user['review_dimension']=='requirements':
                binding=next(b for b in user['requirement_bindings'] if b['mode']=='audience_fit')
                obj={'pass':False,'issues':[{'evidence_id':user['review_evidence'][0]['evidence_id'],'reason':'Нет буквального названия аудитории',
                     'severity':'major','requirement_id':binding['id'],'failure_type':'verbatim_missing'}]}
            else: obj={'pass':True,'issues':[]}
            return {'message':{'content':json.dumps(obj,ensure_ascii=False)}}
        with patch('generic_text_review.request_chat',side_effect=chat) as model:
            result=review.run_quality_cycle(request,{'action':'write','answer':draft},{},
                model='author',reviewer_models=assignment,
                repairer=lambda *a,**k:self.fail('a context label does not justify rewriting the draft'))
        self.assertEqual(result['action'],'write')
        self.assertEqual(model.call_count,4)
        self.assertTrue(result['quality_review']['pass'])
        self.assertEqual(len(result['quality_review']['dismissed_issues']),1)


if __name__=='__main__': unittest.main()
