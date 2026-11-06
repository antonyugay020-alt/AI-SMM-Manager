"""Regression for independent cross-document QA; never uses network."""
import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

from independent_acceptance_editor import parse_verdict, review_saved_results, _review_windows, call_editor

STRATEGY='Практическая стратегия с известными ограничениями.'
CALENDAR='План из двух разных публикаций с CTA и визуалом.'

class AcceptanceEvidenceV3Tests(unittest.TestCase):
    def verdict(self, data):
        return parse_verdict(json.dumps(data,ensure_ascii=False),strategy=STRATEGY,calendar=CALENDAR)

    def test_valid_empty_issue_pass(self):
        self.assertTrue(self.verdict({'pass':True,'issues':[]})['pass'])

    def test_missing_pass_cannot_pass(self):
        with self.assertRaises(ValueError):self.verdict({'issues':[]})

    def test_pass_with_issues_cannot_pass(self):
        self.assertFalse(self.verdict({'pass':True,'issues':[{'document':'strategy','type':'invented_fact','quote':'Практическая','reason':'Данные не подтверждены'}]})['pass'])

    def test_rejection_must_cite_exact_source(self):
        with self.assertRaises(ValueError):self.verdict({'pass':False,'issues':[{'document':'calendar','type':'quality','quote':'выдуманная цитата','reason':'Она отсутствует в документе'}]})

    def test_rejection_without_evidence_fails_closed(self):
        with self.assertRaises(ValueError):self.verdict({'pass':False,'issues':[]})

    def test_rejection_with_real_citation(self):
        obj=self.verdict({'pass':False,'issues':[{'document':'calendar','type':'quality','quote':'План из двух','reason':'Это слишком общая формулировка'}]})
        self.assertFalse(obj['pass'])

    def test_suite_fails_if_independent_editor_discovers_issue(self):
        import independent_acceptance_editor as reviewer
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('one','two'):
                (root/(name+'_synthetic_documents.json')).write_text(json.dumps({
                    'request':'Два поста',
                    'response':{'documents':[{'kind':'strategy','content':STRATEGY},
                                             {'kind':'calendar','content':CALENDAR}]}}),encoding='utf-8')
            def fake_editor(request,strategy,calendar,**kwargs):
                if 'Неизвестная 999' in strategy:
                    return {'pass':False,'issues':[{'document':'strategy','type':'invented_fact',
                            'quote':'Неизвестная 999','reason':'Такой адрес отсутствует в задании'}]}
                if 'снимаем мерки' in calendar.casefold():
                    return {'pass':False,'issues':[{'document':'calendar','type':'invented_fact',
                            'quote':'снимаем мерки','reason':'Операционный процесс не подтверждён источником'}]}
                return {'pass':False,'issues':[{'document':'calendar','type':'quality',
                        'quote':'План из двух','reason':'Недостаточно конкретной пользы'}]}
            def block_unverified(request,findings,**kwargs):
                return {'supported':[],'non_business':[],
                        'blocking':[{**x,'adjudication':'unsupported',
                                     'reason':str(x.get('reason',''))+' Не подтверждено источниками.'}
                                    for x in findings]}
            result=review_saved_results(root,editor_call=fake_editor,claim_adjudicator=block_unverified)
            self.assertEqual(result['status'],'FAIL')
            self.assertEqual(result['negative_control']['status'],'PASS')

    def test_partial_output_is_reviewed_but_never_counted_complete(self):
        import independent_acceptance_editor as reviewer
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            partial='Стратегия с неподтверждённым фактом: в студии гарантируем результат.'
            (root/'workshop_synthetic_documents.json').write_text(json.dumps({
                'request':'Не выдумывай гарантии.',
                'response':{'documents':[{'kind':'strategy','content':partial,'valid':False}]}}),encoding='utf-8')
            seen=[]
            def fake_editor(request,strategy,calendar,**kwargs):
                seen.append((strategy,calendar,kwargs))
                if 'Неизвестная 999' in strategy:
                    return {'pass':False,'issues':[{'document':'strategy','type':'invented_fact',
                        'quote':'Неизвестная 999','reason':'Адрес не подтверждён'}]}
                if 'неподтверждённым фактом' in strategy:
                    return {'pass':False,'issues':[{'document':'strategy','type':'invented_fact',
                        'quote':'гарантируем результат','reason':'Гарантия не подтверждена'}]}
                if 'снимаем мерки' in calendar:
                    return {'pass':False,'issues':[{'document':'calendar','type':'invented_fact',
                        'quote':'снимаем мерки','reason':'Процесс не подтверждён'}]}
                return {'pass':True,'issues':[]}
            with patch.object(reviewer,'call_editor',side_effect=fake_editor):
                result=reviewer.review_saved_results(root)
        case=result['cases'][0]
        self.assertEqual(case['status'],'FAIL')
        self.assertEqual(case['document_coverage']['missing'],['calendar'])
        self.assertTrue(case['verdict']['issues'])
        self.assertTrue(any(s==partial and c=='' for s,c,_ in seen))
        self.assertTrue(any(kwargs.get('allow_partial') is True for _,_,kwargs in seen))
        self.assertEqual(result['negative_control']['status'],'PASS')

    def test_partial_review_scope_is_explicit_and_does_not_allow_empty_documents(self):
        with self.assertRaisesRegex(ValueError,'оба документа'):
            _review_windows('Проверь проект.','Черновик стратегии.','')
        self.assertEqual(_review_windows('Проверь проект.','Черновик стратегии.','',
                                         allow_partial=True),
                         [('Черновик стратегии.','')])
        with self.assertRaisesRegex(ValueError,'Нет документов'):
            _review_windows('Проверь проект.','','',allow_partial=True)

    def test_partial_review_can_inspect_strategy_without_claiming_task_complete(self):
        result=call_editor('План публикаций для проекта.','Цель — привлечь вопросы.','',
                           allow_partial=True,
                           editor_call=lambda req,st,ca,**kwargs:{'pass':True,'issues':[]})
        self.assertTrue(result['pass'])
        with self.assertRaisesRegex(ValueError,'оба документа'):
            call_editor('План публикаций для проекта.','Цель — привлечь вопросы.','')

    def test_suite_requires_negative_control(self):
        import independent_acceptance_editor as reviewer
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('one','two'):
                (root/(name+'_synthetic_documents.json')).write_text(json.dumps({
                    'request':'Два поста',
                    'response':{'documents':[{'kind':'strategy','content':STRATEGY},
                                             {'kind':'calendar','content':CALENDAR}]}}),encoding='utf-8')
            with patch.object(reviewer,'call_editor',return_value={'pass':True,'issues':[]}):
                result=review_saved_results(root)
            self.assertEqual(result['status'],'FAIL')
            self.assertEqual(result['negative_control']['status'],'FAIL')

if __name__=='__main__':unittest.main()
