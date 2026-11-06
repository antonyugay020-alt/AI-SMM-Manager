"""Offline regressions from actual Windows acceptance V6 report.

No Ollama calls; these tests prove contracts, not content quality.
"""
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from factual_claim_audit import scan_unsupported_business_claims
from independent_acceptance_editor import _evidence_chunks, call_editor, parse_verdict, review_saved_results
from independent_quality_gate import final_quality_gate

BRIEF = ('Составь стратегию для мастерской. Нельзя придумывать цены, '
         'адреса, сроки ремонта и гарантии.')
STRATEGY = 'Объяснять клиентам выгоды профилактики велосипеда.'
CALENDAR = 'Пост: запишитесь на диагностику; детали услуги уточните в мастерской.'
CLAIM = ('Проверенный факт о компании: всем клиентам гарантируем результат за 1 день, '
         'адрес ул. Неизвестная 999, цена 50000 рублей.')

class ArchitectureHardeningV7(TestCase):
    def test_negative_control_catches_all_three_unsourced_business_claims(self):
        issues = scan_unsupported_business_claims(BRIEF, STRATEGY+'\n'+CLAIM, CALENDAR)
        self.assertEqual({i['type'] for i in issues}, {'invented_fact','unsafe_claim'})
        self.assertTrue(any('50000 рублей' in i['quote'] for i in issues))
        self.assertTrue(any('Неизвестная 999' in i['quote'] for i in issues))
        self.assertTrue(any('за 1 день' in i['quote'] for i in issues))

    def test_unsourced_guard_does_not_veto_claim_present_in_brief(self):
        specified = BRIEF+' Проверенный адрес: ул. Неизвестная 999. '
        issues = scan_unsupported_business_claims(specified, STRATEGY+' Адрес ул. Неизвестная 999', CALENDAR)
        self.assertEqual(issues,[])

    def test_unsourced_guard_does_not_invent_prohibitions(self):
        request='Придумай рекламную концепцию с вымышленными ценами для фантастического магазина'
        issues = scan_unsupported_business_claims(request,'Цена 50000 рублей','')
        self.assertEqual(issues,[])

    def test_issues_quote_actual_text_from_actual_document(self):
        doc = STRATEGY+'\n'+CLAIM
        for issue in scan_unsupported_business_claims(BRIEF,doc,CALENDAR):
            self.assertIn(issue['quote'], doc if issue['document']=='strategy' else CALENDAR)
            validated = parse_verdict(json.dumps({'pass':False,'issues':[issue]},ensure_ascii=False),
                                      strategy=doc,calendar=CALENDAR)
            self.assertFalse(validated['pass'])

    def test_long_calendar_row_is_one_verbatim_context_unit(self):
        row = '| 1/Пн | Instagram | ' + 'Описание визуального сценария. '*17 + \
              ' | Полный текст публикации: абзац о пользе услуги. | CTA: написать сообщение | KPI: обращения |'
        chunks = _evidence_chunks(STRATEGY, row)
        segments=[v for k,v in chunks.items() if k.startswith('calendar:')]
        self.assertEqual(segments,[row])
        self.assertIn('Полный текст публикации', segments[0])

    def test_model_pass_cannot_launder_proven_unsourced_fact(self):
        # Mock ONLY the model output. Real deterministic claims still block the job.
        with patch('independent_acceptance_editor._call_editor_once',return_value={'pass':True,'issues':[]}):
            result=call_editor(BRIEF,STRATEGY+'\n'+CLAIM,CALENDAR)
        self.assertFalse(result['pass'])
        self.assertGreaterEqual(len(result['issues']), 3)

    def test_negative_control_is_really_checked_not_mislabeled(self):
        with tempfile.TemporaryDirectory() as tmp:
            for case in ('workshop','language_school'):
                data={'request':BRIEF,'response':{'documents':[
                    {'kind':'strategy','content':STRATEGY},
                    {'kind':'calendar','content':CALENDAR}]}}
                (Path(tmp)/(case+'_synthetic_documents.json')).write_text(json.dumps(data),encoding='utf-8')
            def reviewed(request,strategy,calendar,**kwargs):
                if '50000 рублей' in strategy:
                    return {'pass':False,'issues':[{'document':'strategy','type':'invented_fact',
                        'quote':'цена 50000 рублей','reason':'Необоснованная цена'}]}
                if 'снимаем мерки' in calendar.casefold():
                    return {'pass':False,'issues':[{'document':'calendar','type':'invented_fact',
                        'quote':'снимаем мерки','reason':'Процесс мастерской не указан в запросе'}]}
                return {'pass':True,'issues':[]}
            def adjudicate(request, findings, **kwargs):
                # This contract test exercises the reviewer/editor wiring;
                # the independent semantic adjudicator is a separate live
                # acceptance check and must never call local Ollama here.
                return {'supported': [], 'non_business': [],
                        'blocking': [{**x, 'adjudication': 'unsupported'} for x in findings]}
            result=review_saved_results(Path(tmp),editor_call=reviewed,
                                        claim_adjudicator=adjudicate)
        self.assertEqual(result['negative_control']['status'],'PASS')
        self.assertTrue(result['negative_control']['detected'])
        self.assertEqual(result['operational_claim_control']['status'],'PASS')
        self.assertTrue(result['operational_claim_control']['detected'])
        self.assertEqual(result['status'],'PASS')

    def test_editorial_critical_issue_does_not_claim_gui_delivery(self):
        from test_windows_acceptance_v4 import FinalEditorialGateTests
        seed=FinalEditorialGateTests.prepared()
        seed['documents'][0]['content']+='\n'+CLAIM
        issue=scan_unsupported_business_claims(BRIEF,seed['documents'][0]['content'],
                                                 seed['documents'][1]['content'])
        before=seed['documents'][0]['content']
        gated=final_quality_gate(seed,BRIEF,lambda *args:{'pass':False,'issues':issue})
        self.assertFalse(gated['documents'][1]['valid'])
        self.assertEqual(gated['documents'][0]['content'],before)
        self.assertFalse(gated['content_delivery']['task_orchestration']['completed'])
        self.assertEqual(gated['content_delivery']['independent_editor']['status'],'BLOCKED')
