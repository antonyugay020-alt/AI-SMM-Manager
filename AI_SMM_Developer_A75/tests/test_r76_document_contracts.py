"""Compiler content ownership, durable decoder rejection and shared brief roles.

All model decisions in this suite are injected; no live quality claim.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import compound_tasks as compound
from agent_runtime import validate_document
from claim_support import (_quote_options, _source_quote_authority,
                           adjudicate_factual_findings, public_copy_findings)
from deliverable_contract import labelled_context_role, review_requirement_bindings
from document_recovery import DocumentDraft, GeneratedDocument
from evidence_context import build_evidence_context
from platform_registry import canonical_platform_name, canonicalize_platforms, explicit_platforms
from strategy_recovery import produce_checked_strategy
import test_strategy_draft_schema as strategy_fixture


def payload():
    return strategy_fixture.StructuredStrategyDraftTests().valid_payload()


class DocumentContractBoundaries(unittest.TestCase):
    def test_concise_fields_and_alias_labels_are_preserved_without_invented_content(self):
        for channel, expected in [('VK', 'ВКонтакте'), ('VKontakte', 'ВКонтакте'),
                                  ('You Tube', 'YouTube'), ('Telegram', 'Telegram'),
                                  ('НовыйКанал', 'НовыйКанал')]:
            data = payload()
            data.update(audience='IT', strategy_direction='FAQ', channel_plan=channel)
            original = copy.deepcopy(data)
            text = compound._render_strategy_draft(json.dumps(data), channel_context=expected)
            self.assertIn('## Целевая аудитория\nIT', text)
            self.assertIn('## Стратегическое направление\nFAQ', text)
            self.assertIn('## Канал и продвижение\n' + expected, text)
            self.assertNotIn('Планировать публикации в', text)
            self.assertEqual(data, original)
        richer = 'Обсудить вопросы в VK и Telegram после проверки ограничений.'
        self.assertEqual(canonical_platform_name(richer), richer)
        self.assertEqual(explicit_platforms('VKontakte'), ['ВКонтакте'])
        self.assertEqual(canonicalize_platforms(['VK', 'VKontakte']), ['ВКонтакте'])

    def test_empty_sections_and_missing_metrics_still_reject(self):
        for key in ('audience', 'strategy_direction', 'channel_plan'):
            data = payload()
            data[key] = '  '
            with self.assertRaises(ValueError):
                compound._render_strategy_draft(json.dumps(data))
        data = payload()
        data['metrics'] = [{'name': 'Охват'}]
        with self.assertRaises(ValueError):
            compound._render_strategy_draft(json.dumps(data))

    def test_short_complete_document_reaches_independent_usefulness_review(self):
        data = dict(objectives=['Доверие'], audience='IT', strategy_direction='FAQ',
            channel_plan='VK', content_pillars=['Советы'], metrics=[{'name': 'Отклик',
            'method': 'Подсчёт'}], implementation_steps=['Проверить'])
        rendered = compound._render_strategy_draft(json.dumps(data))
        self.assertLess(len(rendered), 450)
        seen = []
        def review(content):
            seen.append(content)
            return {'pass': False, 'issues': [{'quote': 'FAQ', 'reason': 'Нет прикладного объяснения подхода.'}]}
        result = produce_checked_strategy('Разработай стратегию.', 1,
            lambda *_: rendered, validate_document, attempts=1, review=review)
        self.assertEqual(len(seen), 1)
        self.assertFalse(result[1])
        self.assertIn('Нет прикладного объяснения', result[2])

    def test_decoder_error_has_priority_over_normalization_validation_and_review(self):
        raw = '{"audience":"IT","channel_plan":"VK"}'
        error = 'Отсутствуют обязательные метрики'
        saved = []
        forbidden = lambda *args, **kwargs: self.fail('Rejected JSON must not enter prose/quality gates')
        with patch('strategy_recovery.normalize_explicit_cadence', side_effect=forbidden):
            result = produce_checked_strategy('Разработай стратегию.', 1,
                lambda *_: GeneratedDocument(raw, error), forbidden, review=forbidden,
                attempts=1, checkpoint=lambda *row: saved.append(row))
        self.assertEqual(result, (raw, False, 'Ошибка контракта документа: ' + error))
        self.assertEqual(saved, [result])

    def test_exact_raw_and_decoder_reason_survive_checkpoint_reopen_and_targeted_repair(self):
        raw = '{"audience":"IT","channel_plan":"VK"}'
        error = 'Не заполнены метрики или способы их измерения'
        with tempfile.TemporaryDirectory() as home:
            store = DocumentDraft(Path(home), '8' * 64, 'strategy')
            first = produce_checked_strategy('Разработай стратегию.', 1,
                lambda *_: GeneratedDocument(raw, error), lambda *_: (True, ''),
                attempts=1, checkpoint=store.save)
            self.assertFalse(first[1])
            reopened = DocumentDraft(Path(home), '8' * 64, 'strategy').load()
            self.assertEqual(reopened['content'], raw)
            self.assertIn(error, reopened['reason'])
            seen = []
            fixed = compound._render_strategy_draft(json.dumps(payload()))
            def repair(content, reason, attempt):
                seen.append((content, reason))
                return fixed
            result = produce_checked_strategy('Разработай стратегию.', 1,
                lambda *_: self.fail('Do not regenerate persisted raw draft'),
                lambda *_: (True, ''), draft=reopened, repair=repair,
                review=lambda _: {'pass': True, 'issues': []}, max_revision_calls=1,
                checkpoint=store.save)
            self.assertTrue(result[1])
            self.assertEqual(seen, [(raw, reopened['reason'])])
            self.assertEqual(store.load()['history'][-1]['content'], raw)

    def test_compound_entry_keeps_exact_recognized_json_failure_as_cause(self):
        request = 'Разработай SMM-стратегию и контент-план для Telegram на одну неделю: 1 публикация в неделю, всего 1 публикация.'
        for raw in ('{"audience":"IT"}', '{"audience":', '[]'):
            with tempfile.TemporaryDirectory() as home, \
                 patch.object(compound, 'analyze', return_value={'channels': 'Telegram'}), \
                 patch.object(compound, '_ask', return_value=raw), \
                 patch.object(compound, 'validate_document', side_effect=AssertionError('No prose fallback for JSON')), \
                 patch.object(compound, 'plan_slots', side_effect=AssertionError('Rejected strategy must block planning')):
                result = compound.execute_compound(request, dict(project_workspace=home,
                    randomized_acceptance_run=True, strict_document_recovery=True), model='writer')
            self.assertEqual(result['documents'][0]['content'], raw)
            self.assertFalse(result['documents'][0]['valid'])
            self.assertIn('Ошибка контракта документа:', result['documents'][0]['reason'])

    def test_saved_json_is_decoded_before_any_markdown_normalizer(self):
        raw = '{"objectives":["По 1 публикации в неделю"],"metrics":[]}'
        error = compound._decode_strategy_proposal(raw).contract_error
        seen = []
        with patch('strategy_recovery.normalize_explicit_cadence',
                   side_effect=AssertionError('Do not rewrite raw JSON as Markdown')):
            result = produce_checked_strategy('Разработай стратегию.', 2,
                lambda *_: self.fail('Do not regenerate saved raw'), lambda *_: (True, ''),
                draft={'content': raw, 'reason': 'Ошибка контракта документа: ' + error},
                decode=compound._decode_strategy_proposal, attempts=1,
                checkpoint=lambda *row: seen.append(row))
        self.assertEqual(result[0], raw)
        self.assertFalse(result[1])
        self.assertIn(error, result[2])
        self.assertEqual(seen, [result])

    def test_repair_json_uses_same_compiler_and_still_requires_full_review(self):
        raw = '{"objectives":[],"channel_plan":"VK"}'
        fixed = json.dumps(payload(), ensure_ascii=False)
        edits = []
        reviews = []
        def repair(content, reason, attempt):
            edits.append(content)
            return fixed
        def review(content):
            reviews.append(content)
            return {'pass': False, 'issues': [{'quote': 'Рекомендовать', 'reason': 'Подход недостаточно применим.'}]}
        result = produce_checked_strategy('Разработай стратегию.', 1,
            lambda *_: self.fail('Do not regenerate'), validate_document,
            draft={'content': raw, 'reason': 'Ошибка контракта документа: отсутствуют цели'},
            decode=compound._decode_strategy_proposal, repair=repair, max_revision_calls=1,
            review=review, attempts=1)
        self.assertEqual(edits, [raw])
        self.assertEqual(len(reviews), 1)
        self.assertIn('## Целевая аудитория', reviews[0])
        self.assertFalse(result[1])
        self.assertIn('Подход недостаточно применим', result[2])

    def test_brief_roles_and_source_authority_share_one_label_parser(self):
        goals = ('Цель', 'Цель поста', 'Цель публикации', 'Задача контента',
                 'Задача текста', 'Goal', 'Objectives')
        for name in goals:
            for colon in (':', '：', ' — ', ' – ', ' - '):
                text = name + colon + ' сформировать доверие без обещаний быстрого результата.'
                self.assertEqual(labelled_context_role(text), 'objective_fit')
                self.assertEqual(review_requirement_bindings(text)[0]['mode'], 'objective_fit')
                self.assertFalse(_source_quote_authority(text)[0])
        decorated = '**Цель поста**: сформировать доверие.'
        self.assertEqual(labelled_context_role(decorated), 'objective_fit')
        self.assertFalse(_source_quote_authority(decorated)[0])
        for text in ('Аудитория: новые читатели.', 'Tone: calm and clear.'):
            self.assertFalse(_source_quote_authority(text)[0])
        self.assertTrue(_source_quote_authority('Подтверждённая услуга: обучение в малых группах.')[0])

    def test_goal_cannot_enter_quote_enum_or_exact_source_match_as_business_proof(self):
        goal = 'Цель поста: сформировать доверие без обещаний быстрого результата.'
        fact = 'Подтверждённая услуга: разговорные занятия для взрослых.'
        request = fact + ' ' + goal
        row = public_copy_findings('Мы гарантируем быстрый результат.')[0]
        options = _quote_options(build_evidence_context(request).sources, row,
                                 authoritative_only=True)
        self.assertTrue(options)
        self.assertTrue(all('Цель поста' not in option['quote'] for option in options.values()))
        def ask(model, system, user, **kw):
            data = json.loads(user)
            self.assertTrue(all('Цель поста' not in option['quote']
                                for option in data['authorized_quote_options']))
            return {'decisions': {finding['finding_id']: dict(statement_class='service_outcome',
                speech_act='assertion', assertion_subject='business', source_relation='unknown',
                verdict='unsupported', source_quote_id='none', invitation_kind='none',
                reason='The source does not establish the promised outcome.') for finding in data['findings']}}
        result = adjudicate_factual_findings(request, [row], ask=ask, require_semantic_contract=True)
        self.assertEqual(result['blocking'][0]['adjudication'], 'unsupported')
        self.assertFalse(result['supported'])

    def test_literal_requirement_and_business_proof_are_separate(self):
        text = 'Цель поста: упомяни слово «доверие».'
        binding = review_requirement_bindings(text)[0]
        self.assertTrue(binding['literal_directive'])
        self.assertEqual(binding['mode'], 'instruction')
        self.assertEqual(labelled_context_role(text), 'objective_fit')
        self.assertFalse(_source_quote_authority(text)[0])

    def test_generated_document_types_are_explicit_and_immutable(self):
        with self.assertRaises(TypeError):
            GeneratedDocument({}, 'bad')
        with self.assertRaises(TypeError):
            GeneratedDocument('text', None)
        proposal = GeneratedDocument('text', 'bad')
        with self.assertRaises(AttributeError):
            proposal.contract_error = ''
