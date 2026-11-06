from substance_fixture import assessed_reply
"""Whole typed evidence through long-document review, independent of subject/model."""
import copy
import json
import random
import re
import unittest
from unittest.mock import patch

import independent_acceptance_editor as editor
import generic_text_review as generic
from deliverable_contract import editorial_form_bindings


def documents(seed=81,rows=16):
    rng=random.Random(seed)
    strategy='# Strategy\n\n## Audience\n'+('Readers with different needs. '*12)+'\n\n## Approach\n'+('Compare the observed responses. '*70)
    headings=['Week/day','Caption','Visual','CTA','KPI','Measurement','Resources']
    data=['# Calendar','', '| '+' | '.join(headings)+' |','|'+'---|'*len(headings)]
    for index in range(rows):
        values=[f'{index+1}/Mon',f'Question {rng.randrange(100000)}. '+('A complete useful sentence. '*rng.randrange(8,20)),
                'Show a diagram near the outline of the object. '+('Another complete scene. '*rng.randrange(8,20)),
                'Share your question.','Replies.','Count replies in the dashboard.','Prepare an illustration.']
        data.append('| '+' | '.join(values)+' |')
    return strategy,'\n'.join(data)


class TypedEditorialWindows(unittest.TestCase):
    def test_varied_large_documents_cover_original_sentences_roles_and_global_ids(self):
        for seed in range(81,89):
            st,ca=documents(seed)
            original=editor._inventory_segments(st,ca)
            windows=editor._typed_editor_windows('Create strategy and calendar.',st,ca)
            self.assertGreater(len(windows),1)
            collected={row['evidence_id']:row for window in windows for row in window.segments}
            self.assertEqual(collected,{row['evidence_id']:row for row in original})
            for window in windows:
                editor._bound_editor_segments(st,ca,window.segments)
            self.assertTrue(all(row['row_context'].get('slot') and row['field_type']=='visual'
                for row in original if 'outline of the object' in row['content']))

    def test_native_gateway_never_reparses_character_slices(self):
        st,ca=documents()
        events=[];calls=[]
        def chat(payload,**kwargs):
            calls.append(payload['messages'][1]['content'])
            return {'message':{'content':json.dumps(assessed_reply(payload,{'pass':True,'issues':[]}))}}
        with patch.object(editor,'request_chat',side_effect=chat), \
             patch.object(editor,'_split_document',side_effect=AssertionError('raw slicing is forbidden')):
            out=editor.call_editor('Create strategy and calendar.',st,ca,
                report=lambda stage,**details:events.append((stage,details)))
        self.assertTrue(out['pass'])
        observed={ref for stage,d in events if stage=='editor_window_bound' for ref in d['evidence_ids']}
        self.assertEqual(observed,{row['evidence_id'] for row in editor._inventory_segments(st,ca)})
        self.assertGreater(len(calls),1)
        self.assertTrue(any('FIELD ' in content and '"field_type": "visual"' in content for content in calls))

    def test_global_field_locator_survives_late_window_to_host_validation(self):
        st,ca=documents(rows=20)
        target=next(row for row in editor._inventory_segments(st,ca)
                    if row['field_type']=='measurement' and row['row_context'].get('slot')=='20/Mon')
        window=next(w for w in editor._typed_editor_windows('Review.',st,ca) if target in w.segments)
        verdict={'pass':False,'issues':[{'document':'calendar','type':'quality',
            'evidence_id':target['evidence_id'],'reason':'The measurement method is not sufficiently precise.'}]}
        with patch.object(editor,'request_chat',side_effect=lambda payload,**kw:{'message':{'content':json.dumps(assessed_reply(payload,verdict))}}):
            out=editor._call_editor_once('Review.',st,ca,_segments=window.segments)
        issue=out['issues'][0]
        self.assertEqual(issue['quote'],target['content'])
        self.assertEqual(issue['row_context']['slot'],'20/Mon')
        self.assertEqual(issue['field_type'],'measurement')

    def test_tampered_role_id_content_or_duplicate_cannot_authorize_review(self):
        st,ca=documents(rows=1)
        row=editor._inventory_segments(st,ca)[-1]
        for key,value in [('field_type','caption'),('content','Altered text.'),('evidence_id','calendar:9999')]:
            forged=copy.deepcopy(row);forged[key]=value
            with self.assertRaises(ValueError):editor._bound_editor_segments(st,ca,[forged])
        with self.assertRaises(ValueError):editor._bound_editor_segments(st,ca,[row,row])

    def test_a_long_sentence_is_whole_and_over_budget_is_explicit_no_call(self):
        sentence=('Longword '*300).strip()+'.'
        segments=editor._inventory_segments('',sentence)
        self.assertEqual([row['content'] for row in segments],[sentence])
        with patch.object(editor,'request_chat') as chat:
            with self.assertRaisesRegex(ValueError,'предложение'):
                editor.call_editor('Review.','A strategy.',('word '*6000)+'.')
        chat.assert_not_called()

    def test_escaped_pipe_keeps_original_table_field_and_source_bytes(self):
        st='A strategy.'
        ca='| Week/day | Caption | KPI |\n|---|---|---|\n| 1/Mon | Choose A \\| B. | Replies. |'
        row=next(x for x in editor._inventory_segments(st,ca) if 'Choose' in x['content'])
        self.assertEqual(row['content'],'Choose A \\| B.')
        self.assertEqual(row['field_type'],'caption')
        self.assertEqual(row['row_context']['slot'],'1/Mon')

    def test_strategy_metric_is_not_forced_into_publication_column_schema(self):
        st='# Strategy\n## Evaluation\nCompare incoming questions with the previous period.'
        catalog,_=editor._editor_evidence_index(st,'Calendar.')
        ref=next(ref for ref in catalog if ref.startswith('strategy:'))
        raw={'pass':False,'issues':[{'document':'strategy','type':'quality','evidence_id':ref,
            'reason':'The KPI measurement method needs a specified observation period.'}]}
        out=editor.parse_verdict(json.dumps(raw),strategy=st,calendar='Calendar.',
            require_claim=True,host_evidence_only=True,_evidence_catalog=catalog)
        self.assertFalse(out['pass'])
        self.assertEqual(len(out['issues']),1)

    def test_calendar_metric_still_requires_correct_field(self):
        st,ca=documents(rows=1)
        rows=editor._inventory_segments(st,ca)
        row=next(x for x in rows if x['field_type']=='caption')
        catalog,_=editor._editor_evidence_index(st,ca)
        raw={'pass':False,'issues':[{'document':'calendar','type':'quality','evidence_id':row['evidence_id'],
            'reason':'The KPI is not a measurable metric.'}]}
        with self.assertRaisesRegex(ValueError,'KPI/measurement'):
            editor.parse_verdict(json.dumps(raw),strategy=st,calendar=ca,
                require_claim=True,host_evidence_only=True,_evidence_catalog=catalog)

    def test_form_review_gets_artifact_kind_for_each_original_field(self):
        st,ca=documents(rows=2);seen=[]
        def chat(payload,**kwargs):
            data=json.loads(payload['messages'][-1]['content']);seen.append(data)
            return {'message':{'content':json.dumps(assessed_reply(payload,{'pass':True,'issues':[]}))}}
        with patch.object(generic,'request_chat',side_effect=chat):
            for window in editor._typed_editor_windows('Create strategy and calendar.',st,ca):
                editor._review_artifact_form('Create strategy and calendar.',st,ca,model='judge',
                                             _segments=window.segments)
        for data in seen:
            kinds=data['review_scope']['artifact_kinds']
            for row in data['review_evidence']:
                self.assertEqual(kinds[row['field']],row['field'].split(':')[0])
        binding=next(x for x in editorial_form_bindings() if x['id']=='G_editorial_meta')
        self.assertIn('Стратегия',binding['quote']);self.assertIn('caption',binding['quote'])

    def test_invalid_review_still_blocks_without_document_rewriting(self):
        st,ca=documents(rows=2)
        with patch.object(editor,'request_chat',return_value={'message':{'content':'not JSON'}}):
            out=editor.call_editor('Review.',st,ca)
        self.assertFalse(out['pass']);self.assertEqual(out['review_state'],'unavailable')
        self.assertTrue(any(x['type']=='review_unavailable' for x in out['issues']))


if __name__=='__main__':unittest.main()
