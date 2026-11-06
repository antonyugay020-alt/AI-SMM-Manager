import unittest

from claim_support import _is_planning_metadata
from editorial_evidence_matrix import publication_rows, _cell, disproved_missing_field_claim
from independent_acceptance_editor import _editor_evidence_index, _inventory_segments, parse_verdict
import json
from unittest.mock import patch
from publication_schema import CALENDAR_COLUMNS, publication_field_type
from publication_writer import week_to_markdown
from strategy_integrity import normalize_unprovided_category


class SharedPublicationSchemaTests(unittest.TestCase):
    def test_calendar_renderer_preserves_every_post_field_as_its_own_column(self):
        post = {
            'id': '1/Tue', 'topic': 'A distinct topic', 'visual': 'Future staged diagram',
            'caption': 'Publishable caption remains factual-reviewable.',
            'cta': 'Ask whether registration is open.', 'kpi': 'Count questions',
            'measurement': 'Count comments on the post', 'audience': 'Adult beginners',
            'materials': 'Prepare a diagram and a photo',
        }
        slot = {'week': 1, 'day': 'Tue', 'platform': 'Telegram',
                'format': 'Post', 'goal': 'Explain the supplied process'}
        calendar = week_to_markdown([post], [slot])
        lines = calendar.splitlines()
        self.assertEqual(len(CALENDAR_COLUMNS), 11)
        self.assertEqual(len(lines[0].strip('|').split('|')), 11)
        row = publication_rows(calendar)[0]
        self.assertEqual(_cell(row, ('kpi',)), 'Count questions')
        self.assertEqual(_cell(row, ('метод измерения',)), 'Count comments on the post')
        self.assertEqual(_cell(row, ('аудитория',)), 'Adult beginners')
        self.assertEqual(_cell(row, ('материал',)), 'Prepare a diagram and a photo')
        self.assertNotIn('измерение:', _cell(row, ('kpi',)))
        segments = [x for x in _inventory_segments('', calendar) if x['document'] == 'calendar']
        by_type = {x['field_type']: x['content'] for x in segments if x['field_type'] != 'slot'}
        self.assertEqual(by_type['measurement'], 'Count comments on the post')
        self.assertEqual(by_type['audience'], 'Adult beginners')
        self.assertEqual(by_type['materials'], 'Prepare a diagram and a photo')
        self.assertEqual(by_type['caption'], post['caption'])
        catalog, _ = _editor_evidence_index('', calendar)
        measurement_segment = next(x for x in segments if x['field_type'] == 'measurement')
        parsed = parse_verdict(json.dumps({'pass': False, 'issues': [{
            'document': 'calendar', 'type': 'invented_fact',
            'evidence_id': measurement_segment['evidence_id'],
            'claim': measurement_segment['content'], 'reason': 'Check this proposal.'}]}),
            strategy='No business facts.', calendar=calendar, request='Review the documents',
            require_claim=True, host_evidence_only=True, table_cells=True,
            _evidence_catalog=catalog)
        issue = parsed['issues'][0]
        self.assertEqual(issue['field_type'], 'measurement')
        self.assertTrue(_is_planning_metadata(issue))

    def test_typed_plan_fields_do_not_hide_caption_or_cta_claims(self):
        for field in ('visual', 'kpi', 'measurement', 'audience', 'materials', 'topic', 'goal'):
            with self.subTest(field=field):
                self.assertTrue(_is_planning_metadata({'field_type': field, 'quote': 'example'}))
        for field in ('caption', 'cta'):
            with self.subTest(field=field):
                self.assertFalse(_is_planning_metadata({'field_type': field, 'quote': 'example'}))
        self.assertEqual(publication_field_type('Method of measurement'), 'measurement')
        self.assertEqual(publication_field_type('Neобходимые материалы'), 'materials')

    def test_metric_labels_are_not_misclassified_as_caption_text(self):
        for label in ('Количество лайков', 'Количество комментариев', 'Сохранения',
                      'Рост подписчиков', 'Просмотры и охват', 'Метрики вовлечённости'):
            with self.subTest(label=label):
                self.assertEqual(publication_field_type(label), 'kpi')
        self.assertEqual(publication_field_type('Количество подписчиков'), 'kpi')
        self.assertEqual(publication_field_type('Текст подписи к публикации'), 'caption')

    def test_quality_review_reason_must_match_the_cited_publication_field(self):
        post={'id':'1/Tue','topic':'Brake pads','visual':'A neutral staged diagram',
              'caption':'Ask readers what they have noticed.',
              'cta':'Leave a question in the comments.',
              'kpi':'Reach and saves for the post.',
              'measurement':'Read reach and saves from the post statistics.',
              'audience':'New bicycle owners','materials':'Prepare a post image'}
        slot={'week':1,'day':'Tue','platform':'VK','format':'Post','goal':'Invite questions'}
        calendar=week_to_markdown([post],[slot])
        segments=[x for x in _inventory_segments('',calendar) if x['document']=='calendar']
        by_type={x['field_type']:x for x in segments}
        catalog,_=_editor_evidence_index('',calendar)
        def verdict(field_type,reason):
            item=by_type[field_type]
            raw=json.dumps({'pass':False,'issues':[{'document':'calendar','type':'quality',
                'evidence_id':item['evidence_id'],'reason':reason}]})
            return parse_verdict(raw,strategy='Planning only.',calendar=calendar,
                request='Review the post.',require_claim=True,host_evidence_only=True,
                table_cells=True,_evidence_catalog=catalog)
        with self.assertRaisesRegex(ValueError,'KPI/measurement'):
            verdict('caption','Недостаточно конкретизирован показатель охвата публикаций.')
        with self.assertRaisesRegex(ValueError,'KPI/measurement'):
            verdict('cta','Недостаточно конкретизирован показатель сохранений публикаций.')
        with self.assertRaisesRegex(ValueError,'measurement'):
            verdict('kpi','Не указано, как именно измерять и вручную учитывать эти показатели.')
        accepted=verdict('measurement','Не указано, как именно измерять охват публикаций.')
        self.assertEqual(accepted['issues'][0]['field_type'],'measurement')

    def test_invalid_field_evidence_gets_one_bounded_model_correction(self):
        post={'id':'1/Tue','topic':'A neutral topic','visual':'A simple diagram',
              'caption':'Invite readers to share observations.',
              'cta':'Leave a question.','kpi':'Track reach and saves.',
              'measurement':'Read reach and saves from post statistics.',
              'audience':'New readers','materials':'Prepare a diagram'}
        slot={'week':1,'day':'Tue','platform':'VK','format':'Post','goal':'Invite questions'}
        calendar=week_to_markdown([post],[slot])
        segments={item['field_type']:item for item in _inventory_segments('',calendar)}
        replies=[
            {'pass':False,'issues':[{'document':'calendar','type':'quality',
              'evidence_id':segments['caption']['evidence_id'],
              'reason':'Недостаточно конкретизирован показатель сохранений публикации.'}]},
            {'pass':False,'issues':[{'document':'calendar','type':'quality',
              'evidence_id':segments['measurement']['evidence_id'],
              'reason':'Не указано, как именно измерять охват публикации.'}]},
        ]
        calls=[]
        def fake_request(payload,**kwargs):
            calls.append(payload)
            return {'message':{'content':json.dumps(replies.pop(0),ensure_ascii=False)}}
        with patch('independent_acceptance_editor.request_chat',side_effect=fake_request):
            from independent_acceptance_editor import _call_editor_once
            result=_call_editor_once('Review this post.', 'Editorial plan only.', calendar)
        self.assertEqual(len(calls),2)
        self.assertIn('evidence_id',calls[0]['format']['properties']['issues']['items']['properties'])
        self.assertIn('ПЕРЕПРОВЕРЬ',calls[1]['messages'][-1]['content'])
        self.assertEqual(result['issues'][0]['field_type'],'measurement')

    def test_separate_measurement_field_can_disprove_a_false_missing_field_report(self):
        post = {'id': '1/Tue', 'topic': 'T', 'visual': 'V', 'caption': 'C',
                'cta': 'Ask a question', 'kpi': 'Count questions',
                'measurement': 'Count comments', 'audience': 'Adults', 'materials': 'Diagram'}
        slot = {'week': 1, 'day': 'Tue', 'platform': 'Telegram', 'format': 'Post', 'goal': 'G'}
        calendar = week_to_markdown([post], [slot])
        issue = {'document': 'calendar', 'type': 'missing_required_content',
                 'quote': publication_rows(calendar)[0]['raw'],
                 'reason': 'Отсутствует отдельный способ измерения KPI'}
        self.assertTrue(disproved_missing_field_claim(issue, calendar))

    def test_category_normalization_is_idempotent_and_preserves_markup(self):
        draft = '- **Категория:** Ремонт велосипедов\n'
        request = 'Диагностика и замена тормозных колодок.'
        once, edits = normalize_unprovided_category(draft, request)
        twice, repeated_edits = normalize_unprovided_category(once, request)
        self.assertEqual(once, '\n')
        self.assertEqual(twice, once)
        self.assertEqual(len(edits), 1)
        self.assertEqual(repeated_edits, [])


if __name__ == '__main__':
    unittest.main()
