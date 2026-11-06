"""Scoped form policy and typed question-title metadata; replies injected."""
import copy
import io
import json
import unittest
from unittest.mock import Mock, patch

from assertion_scope import requires_business_assertion_review, requires_business_fact_review
from claim_support import adjudicate_factual_findings, public_copy_findings
from deliverable_contract import editorial_form_bindings
from generic_text_review import review_dimension
from review_arbitration import adjudicate_findings
from artifact_review import ReviewContractError


SCOPE = dict(stage='strategy_preflight', available_artifacts=['strategy'], pending_artifacts=['calendar'])


def label(**changes):
    value = dict(statement_class='general_information', verdict='supported', speech_act='label',
                 assertion_subject='general', source_relation='not_needed', source_quote_id='none',
                 invitation_kind='none', reason='A question title identifies this publication, without asserting a company fact.')
    value.update(changes)
    return value


def audit(text, decision):
    seen = []
    def ask(model, system, user, **kwargs):
        rows = json.loads(user)['findings']
        seen.append(rows)
        return {'decisions': {row['finding_id']: copy.deepcopy(decision) for row in rows}}
    out = adjudicate_factual_findings('Напиши полезное объяснение.', public_copy_findings(text),
                                    ask=ask, require_semantic_contract=True)
    return out, seen


class ArtifactBoundaries(unittest.TestCase):
    def test_strategy_and_complete_delivery_have_distinct_host_form_bindings(self):
        before = copy.deepcopy(SCOPE)
        scoped = {r['id']: r for r in editorial_form_bindings(SCOPE)}
        complete = {r['id']: r for r in editorial_form_bindings(dict(
            available_artifacts=['strategy', 'calendar'], pending_artifacts=[]))}
        self.assertIn('G_strategy_form', scoped)
        self.assertNotIn('G_requested_form', scoped)
        self.assertIn('G_requested_form', complete)
        self.assertNotIn('G_strategy_form', complete)
        self.assertIn('явно не назначил их стратегии', scoped['G_strategy_form']['quote'])
        self.assertEqual(SCOPE, before)
        for value in ([], {**SCOPE, 'pending_artifacts': None}, {**SCOPE, 'available_artifacts': [None]}):
            with self.assertRaises(ValueError):
                editorial_form_bindings(value)

    def test_scoped_native_form_gateway_and_arbiter_share_exact_policy(self):
        seen = []
        def wire(req, timeout):
            payload = json.loads(req.data)
            data = json.loads(payload['messages'][-1]['content'])
            seen.append(data)
            refs = {row['id']: row for row in data['requirement_bindings']}
            self.assertIn('G_strategy_form', refs)
            self.assertNotIn('G_requested_form', refs)
            answer = {'pass': False, 'issues': [{'evidence_id': data['review_evidence'][0]['evidence_id'],
                'reason': 'Разделы стратегии не разделены, структура затрудняет чтение.', 'severity': 'major',
                'requirement_id': 'G_strategy_form', 'failure_type': 'style_mismatch'}]}
            return io.BytesIO(json.dumps({'message': {'content': json.dumps(answer)},
                                          'done': True, 'done_reason': 'stop'}).encode())
        with patch('urllib.request.urlopen', side_effect=wire):
            out = review_dimension('Сделай стратегию и календарь.', 'Текущая стратегия.', {},
                'format_and_tone', 'local-reader', artifact_fields={'strategy': 'Текущая стратегия.'},
                artifact_roles={'strategy': 'document_narrative'}, review_scope=SCOPE)
        self.assertFalse(out['pass'])
        finding = {**out['issues'][0], 'document': 'strategy'}
        def judge(model, system, user, **kwargs):
            data = json.loads(user)
            self.assertEqual(data['requirements']['G_strategy_form'], finding['requirement_quote'])
            return {'decisions': {'A001': {'action': 'correct_artifact', 'remedy_scope': 'current',
                'reason': 'The strategy structure requires clear section boundaries.',
                'required_change': 'Разделить существующие разделы стратегии.'}}}
        confirmed, _ = adjudicate_findings(judge, 'other-reader', 'Сделай стратегию и календарь.',
            {'strategy': 'Текущая стратегия.'}, [finding])
        self.assertEqual(confirmed[0]['finding_validation']['remedy_target'], 'strategy')

    def test_unscoped_historical_form_finding_cannot_authorize_strategy_repair(self):
        generic = next(r for r in editorial_form_bindings() if r['id'] == 'G_requested_form')
        finding = dict(document='strategy', quote='Тема будущего материала.',
            reason='Добавить готовые публикации в стратегию.', review_scope=SCOPE,
            requirement_id=generic['id'], requirement_quote=generic['quote'])
        sender = Mock()
        with self.assertRaises(ValueError):
            adjudicate_findings(sender, 'judge', 'Сделай стратегию и календарь.',
                               {'strategy': finding['quote']}, [finding])
        sender.assert_not_called()

    def test_wrong_form_id_in_scoped_review_is_unavailable_not_permission_to_edit(self):
        def response(payload, **kw):
            data = json.loads(payload['messages'][-1]['content'])
            return {'message': {'content': json.dumps({'pass': False, 'issues': [{
                'evidence_id': data['review_evidence'][0]['evidence_id'], 'reason': 'Нет готовых постов.',
                'severity': 'major', 'requirement_id': 'G_requested_form', 'failure_type': 'style_mismatch'}]})}}
        with patch('generic_text_review.request_chat', side_effect=response):
            with self.assertRaises(ReviewContractError):
                review_dimension('Сделай стратегию и календарь.', 'Текущая стратегия.', {},
                    'format_and_tone', 'reader', review_scope=SCOPE)

    def test_completeness_findings_require_the_same_source_owned_request_binding(self):
        captured = []
        def response(payload, **kw):
            data = json.loads(payload['messages'][-1]['content'])
            captured.append(data)
            binding = data['requirement_bindings'][0]
            return {'message': {'content': json.dumps({'pass': False, 'issues': [{
                'evidence_id': data['review_evidence'][0]['evidence_id'],
                'reason': 'Нет полезного объяснения указанной темы.', 'severity': 'major',
                'requirement_id': binding['id'], 'failure_type': 'required_content_missing'}]})}}
        with patch('generic_text_review.request_chat', side_effect=response):
            out = review_dimension('Объясни тему с полезными примерами.', 'Общий рекламный призыв.', {},
                                   'completeness', 'reader')
        self.assertFalse(out['pass'])
        self.assertEqual(out['issues'][0]['requirement_quote'], captured[0]['requirement_bindings'][0]['quote'])
        def unbound(payload, **kw):
            data = json.loads(payload['messages'][-1]['content'])
            return {'message': {'content': json.dumps({'pass': False, 'issues': [{
                'evidence_id': data['review_evidence'][0]['evidence_id'],
                'reason': 'Нет имени проекта.', 'severity': 'major'}]})}}
        with patch('generic_text_review.request_chat', side_effect=unbound):
            with self.assertRaises(ReviewContractError):
                review_dimension('Объясни тему.', 'Общий текст.', {}, 'completeness', 'reader')

    def test_received_and_varied_topic_titles_still_cross_source_review(self):
        texts = ['☕️ Наша кофейная тема сегодня — «Как выбрать напиток по вкусу».',
                 'Наша новая рубрика: «Почему важно уточнять исходные данные».',
                 'Наш заголовок — «Как сформулировать свой вопрос».',
                 'Our topic today: "How to describe a question".']
        for text in texts:
            with self.subTest(text=text):
                row = public_copy_findings(text)[0]
                self.assertTrue(requires_business_fact_review(row))
                self.assertFalse(requires_business_assertion_review(row, label()))
                out, calls = audit(text, label())
                self.assertEqual(len(calls), 1)
                self.assertFalse(out['blocking'])
                self.assertEqual(out['non_business'][0]['quote'], text)

    def test_company_promises_and_service_offers_do_not_become_safe_titles(self):
        texts = ['Наша тема — «Мы гарантируем результат».',
                 'Наша тема — «Как наша команда гарантирует результат».',
                 'Our topic: "How we guarantee success".',
                 'Наша тема — «Когда ответим на все вопросы».',
                 'Наша тема — «Бесплатная услуга каждому».',
                 'Наши специалисты выполняют каждую операцию вручную.',
                 'Our team guarantees success.']
        for text in texts:
            out, _ = audit(text, label())
            self.assertTrue(out['blocking'], text)
            self.assertFalse(out['non_business'], text)

    def test_incomplete_or_inconsistent_title_decisions_still_block(self):
        text = 'Наша тема — «Как сформулировать вопрос».'
        for change in ({'speech_act': 'uncertain'}, {'assertion_subject': 'business'},
                       {'source_relation': 'entails'}, {'source_quote_id': 'foreign'},
                       {'invitation_kind': 'service_access'}, {'verdict': 'uncertain'}):
            out, _ = audit(text, label(**change))
            self.assertTrue(out['blocking'], change)

    def test_a_valid_title_does_not_waive_other_unsupported_business_claims(self):
        text = 'Наша тема — «Как сформулировать вопрос». Мы гарантируем точный результат.'
        def ask(model, system, user, **kw):
            return {'decisions': {row['finding_id']: label() if 'Наша тема' in row['claim'] else
                label(statement_class='service_outcome', speech_act='assertion', assertion_subject='business',
                      source_relation='unknown', verdict='unsupported')
                for row in json.loads(user)['findings']}}
        out = adjudicate_factual_findings('Напиши полезное объяснение.', public_copy_findings(text),
                                         ask=ask, require_semantic_contract=True)
        self.assertEqual(len(out['non_business']), 1)
        self.assertEqual(len(out['blocking']), 1)
        self.assertEqual(out['blocking'][0]['adjudication'], 'unsupported')
