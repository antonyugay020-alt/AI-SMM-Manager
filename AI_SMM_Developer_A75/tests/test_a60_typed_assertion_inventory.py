import json
import unittest
from unittest.mock import patch

from independent_acceptance_editor import (
    IndependentReviewUnavailable,
    _inventory_batches,
    _inventory_business_assertions,
    _inventory_segments,
)
from assertion_scope import requires_business_fact_review
from claim_support import adjudicate_factual_findings


class TypedAssertionInventoryA60(unittest.TestCase):
    def test_first_party_copy_and_visit_calls_always_require_source_audit(self):
        cases=(
            ('В нашей мастерской каждое утро начинается с работы команды.', 'caption'),
            ('У нас в студии всегда звучит музыка.', 'caption'),
            ('Приходите к нам сегодня.', 'cta'),
            ('Come visit us and enjoy the space.', 'caption'),
        )
        for text,field in cases:
            with self.subTest(text=text):
                self.assertTrue(requires_business_fact_review({'content':text,
                    'field_type':field,'field_role':field}))
        self.assertFalse(requires_business_fact_review({'content':
            'Изношенные колодки могут снизить эффективность торможения.',
            'field_type':'caption'}))
        self.assertFalse(requires_business_fact_review({'content':
            'Создать публикацию о короткой паузе.', 'field_type':'topic'}))

    def test_host_forced_copy_claims_cannot_be_suppressed_by_classifier_model(self):
        calendar=('| Неделя/день | Тема | Текст публикации | CTA |\n'
                  '|---|---|---|---|\n'
                  '| 1/Вт | Утренний ритм | В нашей кофейне каждый час начинается с музыки. | '
                  'Приходите и насладитесь атмосферой. |')
        calls=[];host_claimed=[]
        def classify(payload,**kwargs):
            calls.append(payload)
            content=payload['messages'][1]['content']
            rows=json.loads(content[content.index('СЕГМЕНТЫ С ИХ СТРУКТУРНЫМИ ПОЛЯМИ:\n'):
                                 ].split('\n',1)[1])
            return {'message':{'content':json.dumps({'segment_decisions':{
                row['evidence_id']:'not_business_claim' for row in rows}})}}
        def report(event,**details):
            if event=='business_assertion_inventory_host_claimed':
                host_claimed.append(details['evidence_id'])
        with patch('independent_acceptance_editor.request_chat',side_effect=classify):
            found=_inventory_business_assertions('Небольшая кофейня.', '', calendar,
                                                   report=report)
        quotes={item['quote'] for item in found}
        self.assertIn('В нашей кофейне каждый час начинается с музыки.',quotes)
        self.assertIn('Приходите и насладитесь атмосферой.',quotes)
        self.assertEqual(len(host_claimed),2)
        serialized_calls=json.dumps(calls,ensure_ascii=False)
        self.assertNotIn('В нашей кофейне каждый час начинается с музыки.',serialized_calls)
        self.assertNotIn('Приходите и насладитесь атмосферой.',serialized_calls)

    def test_physical_visit_cta_remains_blocking_without_authoritative_source(self):
        finding={'document':'calendar','type':'invented_fact',
            'quote':'Приходите и насладитесь утренней атмосферой с нами.',
            'claim':'Приходите и насладитесь утренней атмосферой с нами.',
            'reason':'Публичное приглашение требует проверки доступности места.',
            'evidence_segment':'Приходите и насладитесь утренней атмосферой с нами.',
            'evidence_id':'calendar:0001','field_role':'CTA','field_type':'cta'}

        def call_as_neutral_invitation(model, system, payload):
            # Even if a source arbiter mistakes a visit for a neutral CTA,
            # host scope must retain the authoritative-source gate.
            return {'decisions':{'F001':{
                'statement_class':'invitation_cta','verdict':'supported',
                'source_quote_id':'none','reason':'Это приглашение посетить место.'}}}

        result=adjudicate_factual_findings('Небольшая кофейня.',[finding],
                                             ask=call_as_neutral_invitation)
        self.assertEqual(len(result['blocking']),1)
        self.assertEqual(result['blocking'][0]['adjudication'],'unsupported')
        self.assertEqual(result['blocking'][0]['statement_class'],'business_fact')
        self.assertEqual(result['non_business'],[])

    def test_batching_keeps_source_segments_whole_and_bounded(self):
        source=[{'evidence_id':f'strategy:{i:04d}','content':'x'*100}
                for i in range(1,31)]
        batches=list(_inventory_batches(source,max_items=7,max_chars=650))
        self.assertEqual([x['evidence_id'] for b in batches for x in b],
                         [x['evidence_id'] for x in source])
        self.assertTrue(all(len(batch)<=7 for batch in batches))
        self.assertTrue(all(sum(len(x['content']) for x in batch)<=650 for batch in batches))


    def test_many_short_segments_fit_in_one_bounded_classification_call(self):
        source=[{'evidence_id':f'strategy:{i:04d}','content':f'Короткий факт {i}.'}
                for i in range(1,41)]
        batches=list(_inventory_batches(source))
        self.assertEqual(len(batches),3)
        self.assertEqual([row['evidence_id'] for batch in batches for row in batch],
                         [row['evidence_id'] for row in source])
        self.assertTrue(all(len(batch)<=16 for batch in batches))
        self.assertTrue(all(sum(len(row['content']) for row in batch)<=5000
                            for batch in batches))

    def test_large_inventory_still_splits_without_losing_or_rewriting_segments(self):
        source=[{'evidence_id':f'strategy:{i:04d}','content':'x'*1200}
                for i in range(1,13)]
        batches=list(_inventory_batches(source))
        flattened=[row for batch in batches for row in batch]
        self.assertGreater(len(batches),1)
        self.assertEqual(flattened,source)
        self.assertTrue(all(sum(len(row['content']) for row in batch)<=5000
                            for batch in batches))

    def test_inventory_covers_every_typed_field_once_and_only_routes_business_claims(self):
        strategy=('## Услуги\nВ мастерской есть собственная печь.\n'
                  '## Цель публикации\nОбъяснить этапы работы с глиной.\n')
        calendar=('| Неделя/день | Тема | Текст публикации | Сценарий/визуал |\n'
                  '|---|---|---|---|\n'
                  '| 1/Вт | Как выбрать глину | Глина бывает разной пластичности. | '
                  'Предлагаем показать вымышленную сцену в студии. |')
        segments=_inventory_segments(strategy,calendar)
        requests=[]
        host_classified=set()

        def classify(payload, **kwargs):
            import copy
            requests.append(copy.deepcopy(payload))
            content=payload['messages'][1]['content']
            start=content.index('СЕГМЕНТЫ С ИХ СТРУКТУРНЫМИ ПОЛЯМИ:\n')
            rows=json.loads(content[start:].split('\n',1)[1])
            answer={'segment_decisions':{
                row['evidence_id']:'business_claim' if 'собственная печь' in row['text'].lower()
                    else 'not_business_claim' for row in rows}}
            return {'message':{'content':json.dumps(answer,ensure_ascii=False)}}

        def report(event,**details):
            if event=='business_assertion_inventory_host_classified':
                host_classified.add(details['evidence_id'])
        with patch('independent_acceptance_editor.request_chat',side_effect=classify):
            found=_inventory_business_assertions('Студия лепки из глины.',strategy,calendar,
                                                   report=report)
        self.assertEqual([x['quote'] for x in found],['В мастерской есть собственная печь.'])
        self.assertEqual(found[0]['field_role'],'Услуги')
        covered=[]
        for payload in requests:
            body=payload['messages'][1]['content']
            rows=json.loads(body[body.index('СЕГМЕНТЫ С ИХ СТРУКТУРНЫМИ ПОЛЯМИ:\n'):
                                  ].split('\n',1)[1])
            covered.extend(row['evidence_id'] for row in rows)
            self.assertLessEqual(len(rows),16)
        all_ids={x['evidence_id'] for x in segments}
        self.assertEqual(set(covered)|host_classified,all_ids)
        self.assertEqual(len(covered),len(set(covered)))
        self.assertFalse(set(covered)&host_classified)

    def test_missing_or_duplicated_segment_classification_blocks_inventory(self):
        strategy='Компания проводит занятия. Второе предложение о работе проекта.'
        def incomplete(payload, **kwargs):
            return {'message':{'content':json.dumps({'segment_decisions':{}})}}
        with patch('independent_acceptance_editor.request_chat',side_effect=incomplete):
            with self.assertRaises(IndependentReviewUnavailable):
                _inventory_business_assertions('Проверь.',strategy,'')

    def test_incomplete_batch_is_split_and_recovered_without_dropping_segments(self):
        strategy='Компания проводит занятия. Второе предложение о работе проекта.'
        calls=[]
        def flaky(payload, **kwargs):
            calls.append(payload)
            allowed=payload['format']['properties']['segment_decisions']['properties']
            if len(allowed)>1:
                return {'message':{'content':'{broken json'}}
            return {'message':{'content':json.dumps({'segment_decisions':{
                ref:'not_business_claim' for ref in allowed}})}}
        events=[]
        with patch('independent_acceptance_editor.request_chat',side_effect=flaky):
            result=_inventory_business_assertions('Проверь.',strategy,'',
                report=lambda stage,**data:events.append((stage,data)))
        self.assertEqual(result,[])
        self.assertTrue(any(stage=='business_assertion_inventory_batch_split' for stage,_ in events))
        self.assertEqual(len(calls),4)

    def test_empty_inventory_answer_gets_one_bounded_higher_budget_retry(self):
        payloads=[]
        def flaky(payload, **kwargs):
            import copy
            payloads.append(copy.deepcopy(payload))
            if len(payloads)==1:
                return {'message':{'content':''}}
            import json
            body=payload['messages'][1]['content']
            rows=json.loads(body.split('СЕГМЕНТЫ С ИХ СТРУКТУРНЫМИ ПОЛЯМИ:\n',1)[1])
            return {'message':{'content':json.dumps({'segment_decisions':{
                row['evidence_id']:'not_business_claim' for row in rows}})}}
        with patch('independent_acceptance_editor.request_chat',side_effect=flaky):
            found=_inventory_business_assertions('Проверь.','Только стратегия.', '')
        self.assertEqual(found,[])
        self.assertEqual([p['options']['num_predict'] for p in payloads],[1200,2400])


if __name__=='__main__':
    unittest.main()
