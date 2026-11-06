import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from document_recovery import patch_document
from independent_acceptance_editor import parse_verdict, _evidence_chunks
from strategy_integrity import separate_embedded_publication_section
from strategy_recovery import produce_checked_strategy


class A49ArchitectureRecoveryTests(unittest.TestCase):
    def test_editor_may_return_prose_without_changing_host_owned_list_marker(self):
        original = "# Plan\n\n- Original claim needs correction.\n"

        def ask(*args, **kwargs):
            return json.dumps({'changes':[{'line_id':'L00003',
                'text':'Replace the unsupported claim.\nAdd a concise next step.'}]})

        result = patch_document(ask, 'qwen3:8b', 'request', original,
                                'unsupported claim', constrained=True)
        self.assertIn('- Replace the unsupported claim.\n  Add a concise next step.\n', result)

    def test_strategy_scope_separator_removes_only_complete_calendar_section(self):
        raw = ('```markdown\n# Strategy\n\n## Goals\nExplain the service.\n\n'
               '## Контент-план (2 публикации)\n### Публикация 1\n'
               '#### Уникальный полный текст:\nCopy one.\n### Публикация 2\n'
               '#### Уникальный полный текст:\nCopy two.\n\n## Risks\nReview unknown facts.\n```')
        result, edits = separate_embedded_publication_section(raw)
        self.assertEqual(len(edits), 1)
        self.assertIn('## Goals', result)
        self.assertIn('## Risks', result)
        self.assertNotIn('Copy one', result)
        self.assertNotIn('## Контент-план', result)
        self.assertTrue(result.rstrip().endswith('```'))

    def test_strategy_scope_separator_uses_repeated_ready_copy_not_one_heading_name(self):
        raw = ('# Strategy\n## Goals\nExplain the service.\n'
               '## 6. Контент-матрица\n### Публикация 1\n'
               '#### Уникальный полный текст:\nCopy one.\n'
               '### Публикация 2\n#### Уникальный полный текст:\nCopy two.\n'
               '## Risks\nReview unknown facts.')
        result, edits = separate_embedded_publication_section(raw)
        self.assertEqual(len(edits), 1)
        self.assertIn('## Goals', result)
        self.assertIn('## Risks', result)
        self.assertNotIn('Copy one', result)
        self.assertNotIn('Контент-матрица', result)

    def test_accepted_strategy_is_never_scope_rewritten(self):
        raw = ('# Strategy\n## Контент-план\n### Публикация 1\n'
               '#### Полный текст\nA\n### Публикация 2\n#### Полный текст\nB')
        result = produce_checked_strategy('task', 2, lambda *a: self.fail('must not generate'),
            lambda *a: (False, 'scope'), existing=raw, attempts=1)
        self.assertEqual(result[0], raw)
        self.assertFalse(result[1])

    def test_host_selected_table_evidence_is_not_rejected_by_duplicate_normalization_check(self):
        strategy = '# Strategy\n'
        calendar = ('| Day | Caption |\n|---|---|\n'
                    '| Tue | Prepare a list of programs and files. |\n')
        catalog = _evidence_chunks(strategy, calendar, table_cells=True)
        ref = next(key for key,value in catalog.items()
                   if value == 'Prepare a list of programs and files.')
        raw = json.dumps({'pass':False,'issues':[{'document':'calendar','type':'quality',
            'evidence_id':ref,'claim':'different model wording',
            'reason':'A specific quality concern.'}]})
        result = parse_verdict(raw,strategy=strategy,calendar=calendar,require_claim=True,
            host_evidence_only=True,table_cells=True,_evidence_catalog=catalog)
        self.assertEqual(result['issues'][0]['claim'],catalog[ref])

    def test_live_editor_prompt_uses_one_consistent_host_id_citation_contract(self):
        from independent_acceptance_editor import _call_editor_once
        payloads=[]
        def chat(payload,**kwargs):
            payloads.append(payload)
            return {'message':{'content':json.dumps({'pass':True,'issues':[]})}}
        with patch('independent_acceptance_editor.request_chat',side_effect=chat):
            _call_editor_once('Check these documents.','Strategy.','Calendar.')
        system=payloads[0]['messages'][0]['content']
        self.assertIn('Не возвращай claim или quote',system)
        self.assertIn('Программа сама добавит исходный текст выбранного сегмента',system)
        self.assertNotIn('claim должен быть конкретной ошибочной фразой',system)
        self.assertNotIn('не подставляй ID публикации вместо claim',system)

    def test_host_evidence_only_uses_selected_segment_as_canonical_claim(self):
        strategy = '# Strategy\nOne exact audited sentence.'
        calendar = 'A calendar caption.'
        catalog = _evidence_chunks(strategy, calendar)
        ref = next(key for key,value in catalog.items() if value == 'One exact audited sentence.')
        raw = json.dumps({'pass':False,'issues':[{'document':'calendar','type':'quality',
            'evidence_id':ref,'claim':'model-authored text that is not evidence',
            'reason':'A specific quality concern.'}]})
        result = parse_verdict(raw,strategy=strategy,calendar=calendar,require_claim=True,
            host_evidence_only=True,_evidence_catalog=catalog)
        self.assertFalse(result['pass'])
        self.assertEqual(result['issues'][0]['claim'],'One exact audited sentence.')
        self.assertEqual(result['issues'][0]['document'],'strategy')


if __name__ == '__main__':
    unittest.main()
