"""Source-led public copy and outcome-led planning across unrelated briefs."""
import io
import json
import random
import unittest
from unittest.mock import patch

import generic_text_review as review
from claim_support import public_copy_findings
from deliverable_contract import planning_request_contract
from ollama_resilience import request_chat, StructuredResponseError
from semantic_brief import plan_slots, propose_topic_replacement
from plan_governance import govern_plan


def source_reply(payload, classify=None, **kwargs):
    user = json.loads(payload['messages'][1]['content'])
    decisions = {}
    for item in user['findings']:
        decisions[item['finding_id']] = (classify(item, user) if classify else {'invitation_kind':'none',
            'statement_class': 'general_information','speech_act':'assertion','assertion_subject':'general','source_relation':'not_needed', 'verdict': 'supported',
            'source_quote_id': 'none', 'reason': 'Общее пояснение для читателя.'})
    return {'message': {'content': json.dumps({'decisions': decisions}, ensure_ascii=False)}}


class PublicCopySourceAuditTests(unittest.TestCase):
    def test_all_public_fragments_keep_verbatim_provenance_and_headings(self):
        text = '## Наши условия: цена 27.50\n1. Мы обрабатываем файлы.\nОбщий пример: схема A → B. 🐾'
        rows = public_copy_findings(text)
        self.assertEqual(len({r['evidence_id'] for r in rows}), len(rows))
        self.assertTrue(any(r['quote'].startswith('## Наши условия') for r in rows))
        self.assertNotIn('1.', [r['quote'] for r in rows])
        for row in rows:
            self.assertIn(row['quote'], text)
            self.assertEqual(row['evidence_segment'], row['quote'])
            self.assertEqual(row['field_type'], 'caption')
            self.assertIn(row['quote'], row['surrounding_context'])
        self.assertEqual(''.join(''.join(r['quote'].split()) for r in rows), ''.join(text.split()))

    def test_first_party_claim_cannot_be_waived_as_general_knowledge_across_domains(self):
        claims = ['Мы начинаем с геометрических фигур.',
                  'Мы сортируем заявки по цвету конверта.',
                  'Our team scans every parcel twice.',
                  'В нашей команде все ответы проходят ручную сверку.']
        for text in claims:
            with self.subTest(text=text), patch('generic_text_review.request_chat', side_effect=source_reply) as call:
                result = review.review_dimension('Напиши текст. Подтверждено только название проекта.',
                                                 text, {}, 'factual_grounding', 'independent')
            self.assertFalse(result['pass'])
            self.assertTrue(result['review_error'])
            self.assertEqual(result['issues'], [])
            self.assertEqual(result['grounding_audit']['blocking'][0]['quote'], text)
            self.assertEqual(result['grounding_audit']['blocking'][0]['adjudication'], 'unavailable')
            self.assertEqual(call.call_count, 2)

    def test_exact_confirmed_business_fact_needs_no_reinterpretation(self):
        text = 'Мы принимаем заявки по предварительной записи.'
        with patch('generic_text_review.request_chat') as model:
            result = review.review_dimension(text, text, {}, 'factual_grounding', 'independent')
        self.assertTrue(result['pass'])
        self.assertEqual(len(result['grounding_audit']['supported']), 1)
        model.assert_not_called()

    def test_verified_material_is_authority_and_prior_ai_document_is_not(self):
        text = 'Мы сортируем заявки по цвету конверта.'
        for verified in (True, False):
            context = {'verified_project_materials': [{'content': text, 'verified': verified}],
                       'existing_documents': [{'content': text}]}
            with self.subTest(verified=verified), patch('generic_text_review.request_chat', side_effect=source_reply):
                result = review.review_dimension('Напиши краткий пост.', text, context,
                                                 'factual_grounding', 'independent')
            self.assertEqual(result['pass'], verified)

    def test_boolean_fact_pass_is_insufficient_and_does_not_trigger_repair(self):
        draft = 'Мы выдаём каждому клиенту персональный набор.'
        assignments = dict(zip(review.CHECKS, ['r1', 'r2', 'r3', 'r4']))
        with patch('generic_text_review.request_chat', return_value={'message': {'content':
                '{"pass":true,"issues":[]}'}}) as model:
            result = review.run_quality_cycle('Напиши краткий текст.', {'action': 'write', 'answer': draft},
                {}, model='author', reviewer_models=assignments,
                repairer=lambda *a, **k: self.fail('An unavailable source audit must not rewrite the author'))
        # Review count depends on whether a second independent panel is available.
        # Either way, an unavailable fact audit must block without author repair.
        self.assertGreaterEqual(model.call_count, 5)
        self.assertLessEqual(model.call_count, 12)
        self.assertEqual(result['action'], 'write_blocked')
        self.assertEqual(set(result['quality_review']['review_errors']), {'factual_grounding'})
        self.assertEqual(result['quality_review']['issues'], [])

    def test_partial_source_failure_preserves_confirmed_and_rejected_siblings(self):
        draft = 'Мы сортируем заявки по цвету конверта. Мы фотографируем каждый конверт.'
        def partial(payload, **kwargs):
            obj = source_reply(payload)
            verdict = json.loads(obj['message']['content'])
            verdict['decisions'].pop(next(reversed(verdict['decisions'])))
            for row in verdict['decisions'].values():
                row.update(statement_class='business_fact',speech_act='assertion',assertion_subject='business',source_relation='unrelated',verdict='unsupported')
            return {'message': {'content': json.dumps(verdict)}}
        with patch('generic_text_review.request_chat', side_effect=partial):
            result = review.review_dimension('Напиши текст.', draft, {}, 'factual_grounding', 'independent')
        self.assertFalse(result['pass'])
        self.assertTrue(result['review_error'])
        self.assertEqual(len(result['issues']), 1)
        self.assertEqual(len(result['grounding_audit']['blocking']), 2)

    def test_educational_example_without_business_actor_is_not_invented_business_process(self):
        draft = 'Рассмотрим условный пример с кругом. Штриховка помогает передать объём.'
        with patch('generic_text_review.request_chat', side_effect=source_reply):
            result = review.review_dimension('Напиши полезное объяснение.', draft, {},
                                             'factual_grounding', 'independent')
        self.assertTrue(result['pass'])
        self.assertEqual(result['issues'], [])
        self.assertEqual(len(result['grounding_audit']['non_business']), len(public_copy_findings(draft)))

    def test_topical_source_cannot_authorize_detailed_process_disguised_as_general(self):
        def weak(item, user):
            return {'invitation_kind':'none','statement_class': 'general_information','speech_act':'assertion','assertion_subject':'general','source_relation':'not_needed', 'verdict': 'supported',
                    'source_quote_id': item['allowed_quote_ids'][-1],
                    'reason': 'Выбрана цитата, описывающая деятельность проекта.'}
        draft = 'Мы обрабатываем заявки лазером и отправляем персональный отчёт.'
        with patch('generic_text_review.request_chat', side_effect=lambda p, **k: source_reply(p, weak)):
            result = review.review_dimension('Подтверждено: мы принимаем заявки.', draft, {},
                                             'factual_grounding', 'independent')
        self.assertFalse(result['pass'])
        self.assertEqual(result['grounding_audit']['blocking'][0]['decision_diagnostics']
                         ['source_relation'], 'not_needed')


class OutcomePlanningTests(unittest.TestCase):
    def test_contract_preserves_each_original_objective_without_industry_templates(self):
        rng = random.Random(54026)
        for _ in range(50):
            token = ''.join(rng.choice('абвгxyz') for _ in range(12))
            request = f'Подготовь план. Цель контента: объяснить {token} и собрать вопросы.'
            # This label is deliberately ambiguous; it must remain a full
            # source requirement even if not recognized as a context field.
            contract = planning_request_contract(request, 1)
            self.assertTrue(all(x['quote'] in request for x in contract['source_requirements']))
            self.assertIn(f'объяснить {token} и собрать вопросы', repr(contract))
            self.assertEqual(contract['total_slots'], 1)

    def test_creation_and_governance_share_source_contract_and_keep_all_goal_fallbacks(self):
        request = 'План для Telegram. Задача контента: объяснить критерии и получить вопросы.'
        brief = {'channels': 'Telegram', 'goals': ['Объяснить критерии', 'Получить вопросы']}
        seen = []
        def propose(model, system, user):
            data = json.loads(user); seen.append(data)
            self.assertNotIn('editorial_perspectives', data)
            return json.dumps({'posts': [{'topic': 'Какие критерии сравнить перед выбором',
                                         'brief': 'Пояснить каждый критерий и предложить вопрос.'}]})
        slots = plan_slots(propose, 'author', request, '', brief, 1, per_week=1, first_pass_only=True)
        self.assertIn('Объяснить критерии', slots[0]['goal'])
        self.assertIn('Получить вопросы', slots[0]['goal'])
        def auditor(model, system, user, **kwargs):
            data = json.loads(user)
            self.assertEqual(data['request_contract'], seen[0]['request_contract'])
            return '{"issues":[]}'
        _, unresolved, available = govern_plan(auditor, 'author', request, brief, '', slots, set(),
                                               expected_ids=['1/Ср'], review_model='reviewer', allow_replan=False)
        self.assertTrue(available)
        self.assertEqual(unresolved, {})

    def test_replacement_does_not_misrepresent_one_slot_as_whole_plan(self):
        slot = {'week': 1, 'day': 'Ср', 'platform': 'Telegram', 'format': 'Пост',
                'topic': 'Изначальная отдельная тема', 'brief': 'Исходный brief',
                'goal': 'Полная цель', 'audience': 'Читатели'}
        def ask(model, system, user):
            data = json.loads(user)
            self.assertIsNone(data['request_contract']['total_slots'])
            return json.dumps({'alternatives': [{'topic': 'Новая самостоятельная тема обсуждения',
                'brief': 'Конкретный разбор критериев.', 'goal': 'Полная цель', 'audience': 'Читатели'}]})
        candidate = propose_topic_replacement(ask, 'author', 'Подготовь план.', '', {}, slot,
                                               ['Тема другого слота'], ['Несоответствие цели'])
        self.assertEqual(candidate['day'], slot['day'])


class ResponseDiagnosticsTests(unittest.TestCase):
    def test_empty_cloud_result_retains_content_free_truncation_evidence_without_retry(self):
        response = {'message': {'content': '', 'thinking': 'PRIVATE_THINKING', 'role': 'assistant'},
                    'done': True, 'done_reason': 'length', 'eval_count': 128}
        events = []
        with patch('ollama_resilience.urllib.request.urlopen', return_value=io.BytesIO(
                json.dumps(response).encode())) as transport:
            with self.assertRaises(StructuredResponseError) as caught:
                request_chat({'model': 'arbitrary:cloud', 'format': 'json', 'messages': []},
                             stage='probe', attempts=2,
                             report=lambda name, **d: events.append(d))
        self.assertEqual(transport.call_count, 1)
        diagnostic = caught.exception.response_diagnostics
        self.assertEqual(diagnostic['thinking_chars'], len('PRIVATE_THINKING'))
        self.assertEqual(diagnostic['done_reason'], 'length')
        self.assertEqual(diagnostic['content_chars'], 0)
        self.assertNotIn('PRIVATE_THINKING', repr(diagnostic) + repr(events))
        self.assertEqual(events[0]['response_diagnostics'], diagnostic)


if __name__ == '__main__':
    unittest.main()
