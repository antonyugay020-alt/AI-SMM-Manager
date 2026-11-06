"""Whole public spans precede prefix heuristics; labels still carry meaning.

Replies here are injected. This suite proves software contracts, not arbitrary
semantic accuracy of a live reviewer.
"""
import copy
import io
import json
import unittest
from unittest.mock import patch

from assertion_scope import classify_obvious_non_business
from claim_interpretation import bind_interpretation
from claim_support import adjudicate_factual_findings, public_copy_findings


def label(**changes):
    value = dict(statement_class='general_information', verdict='supported',
                 speech_act='label', assertion_subject='general',
                 source_relation='not_needed', source_quote_id='none',
                 invitation_kind='none', reason='The label identifies a topic without asserting a company condition.')
    value.update(changes)
    return value


def audit(text, decision):
    calls = []
    def ask(model, system, user, **kwargs):
        payload = json.loads(user)
        calls.append((payload, kwargs['json_schema']))
        return {'decisions': {row['finding_id']: copy.deepcopy(decision)
                              for row in payload['findings']}}
    result = adjudicate_factual_findings('Write a useful public explanation.',
        public_copy_findings(text), ask=ask, require_semantic_contract=True)
    return result, calls


class PublicScopeContract(unittest.TestCase):
    def test_all_public_prefix_shortcuts_route_complete_company_spans(self):
        phrases = (
            'Если у вас есть вопрос о возможности ремонта вашей пары – просто напишите нам, и мы с удовольствием всё обсудим!',
            'Если у вас есть вопрос, напишите, и мы согласуем каждый этап.',
            'Что вы хотели бы уточнить, если наша команда оформит все документы?',
            'Проверьте список, а наша команда перенесёт все данные.',
            'Уточнить стоимость: мы согласуем её без дополнительных платежей.',
            'Неизвестна цена, но наша команда выполняет любые работы.',
            'Если у вас есть вопрос, компания согласует каждый этап.',
            'What would you like to know before our team ships your order?',
        )
        for kind in ('caption', 'cta'):
            for text in phrases:
                with self.subTest(kind=kind, text=text):
                    self.assertIsNone(classify_obvious_non_business(
                        {'quote': text, 'field_type': kind}))

    def test_typed_reader_cannot_hide_explicit_company_continuation(self):
        text = 'Если у вас есть вопрос, напишите нам, и мы согласуем каждый этап.'
        decision = label(statement_class='invitation_cta', speech_act='invitation',
                         assertion_subject='reader', invitation_kind='communication')
        result, calls = audit(text, decision)
        self.assertEqual(len(calls), 2)  # Initial answer + one contract correction.
        self.assertFalse(result['non_business'])
        self.assertFalse(result['supported'])
        self.assertEqual(result['blocking'][0]['adjudication'], 'unavailable')
        self.assertEqual(result['blocking'][0]['decision_diagnostics']['reason'], decision['reason'])

    def test_company_continuation_is_repairable_rejection_not_outage(self):
        text = 'Если у вас есть вопрос, напишите нам, и мы согласуем каждый этап.'
        decision = label(statement_class='operational_process', speech_act='assertion',
                         assertion_subject='business', source_relation='unknown', verdict='unsupported')
        result, calls = audit(text, decision)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['blocking'][0]['adjudication'], 'unsupported')
        self.assertFalse(result['non_business'])

    def test_complete_performative_question_and_bare_cta_remain_narrow(self):
        for text in ('Напишите нам', 'Задайте нам вопрос', 'Спросите нас о записи'):
            self.assertEqual(classify_obvious_non_business(
                {'quote': text, 'field_type': 'caption'})['statement_class'], 'invitation_cta')
        self.assertEqual(classify_obvious_non_business({'quote':
            'Мы хотим узнать: какие вопросы важны для вас?', 'field_type': 'caption'})
            ['statement_class'], 'audience_prompt')
        for text in ('Мы хотим узнать: как наша команда согласует каждый этап?',
                     'Как мы помогаем быть увереннее?'):
            self.assertIsNone(classify_obvious_non_business({'quote': text, 'field_type': 'caption'}))

    def test_labels_cross_actual_gateway_schema_decoder_and_validator(self):
        seen = []
        def wire(req, timeout):
            self.assertEqual(req.full_url, 'http://127.0.0.1:11434/api/chat')
            payload = json.loads(req.data)
            schema = (payload['tools'][0]['function']['parameters']
                      if payload.get('tools') else payload['format'])
            data = json.loads(payload['messages'][-1]['content'])
            seen.append(data)
            rows = {}
            for row in data['findings']:
                prop = schema['properties']['decisions']['properties'][row['finding_id']]
                self.assertIn('label', prop['properties']['speech_act']['enum'])
                self.assertIn('speech_act', prop['required'])
                rows[row['finding_id']] = label()
            answer = {'decisions': rows}
            message = ({'content': '', 'tool_calls': [{'function': {
                'name': 'return_structured_result', 'arguments': answer}}]}
                if payload.get('tools') else {'content': json.dumps(answer)})
            return io.BytesIO(json.dumps({'message': message, 'done': True,
                                          'done_reason': 'stop'}).encode())
        for model in ('arbitrary-local:7b', 'arbitrary-remote:cloud'):
            with patch('urllib.request.urlopen', side_effect=wire):
                result = adjudicate_factual_findings('Write a public explanation.',
                    public_copy_findings('💬 #Тема #UsefulTopic'), model=model)
            self.assertFalse(result['blocking'])
            self.assertEqual(len(result['non_business']), 1)
        self.assertEqual(len(seen), 2)

    def test_label_form_does_not_waive_company_conditions_or_uncertainty(self):
        for text in ('#БесплатнаяДоставка', '#GuaranteedResult', '#ОплатаПослеРаботы'):
            self.assertIsNone(classify_obvious_non_business({'quote': text, 'field_type': 'caption'}))
            result, calls = audit(text, label(statement_class='commercial_condition',
                assertion_subject='business', verdict='unsupported', source_relation='unknown'))
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['blocking'][0]['adjudication'], 'unsupported')
        result, _ = audit('#НеяснаяМетка', label(speech_act='uncertain', verdict='uncertain'))
        self.assertEqual(result['blocking'][0]['adjudication'], 'uncertain')
        self.assertFalse(result['non_business'])

    def test_label_with_invalid_roles_is_not_accepted(self):
        for decision in (label(invitation_kind='communication'),
                         label(source_quote_id='foreign'), label(speech_act='unknown')):
            result, calls = audit('#ОбщаяТема', decision)
            self.assertEqual(len(calls), 2)
            self.assertEqual(result['blocking'][0]['adjudication'], 'unavailable')
        original = label()
        self.assertEqual(bind_interpretation(original, require=True), original)

    def test_partial_sibling_does_not_erase_completed_label(self):
        rows = public_copy_findings('#ОбщаяТема\nМы согласуем каждый этап.')
        self.assertEqual(len(rows), 2)
        seen = []
        def ask(model, system, user, **kwargs):
            findings = json.loads(user)['findings']
            seen.append([row['claim'] for row in findings])
            return {'decisions': {row['finding_id']: label() if row['claim'].startswith('#')
                                 else {} for row in findings}}
        result = adjudicate_factual_findings('Write a public explanation.', rows,
            ask=ask, require_semantic_contract=True)
        self.assertEqual([len(batch) for batch in seen], [2, 1])
        self.assertEqual(result['non_business'][0]['quote'], '#ОбщаяТема')
        self.assertEqual(result['blocking'][0]['quote'], 'Мы согласуем каждый этап.')
        self.assertEqual(result['blocking'][0]['adjudication'], 'unavailable')
