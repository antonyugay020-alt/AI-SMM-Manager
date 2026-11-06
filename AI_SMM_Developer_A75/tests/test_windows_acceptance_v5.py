from substance_fixture import assessed_reply
"""V5 independent evidence IDs, no unobserved silent context truncation."""
import io
import json
import unittest
from unittest.mock import patch
from independent_acceptance_editor import (
    parse_verdict, call_editor, _numbered_document, _evidence_chunks,
    _split_document, _review_windows, EDITOR_CHARACTER_BUDGET,
)


class EvidenceIdTests(unittest.TestCase):
    def test_host_authored_reference_extracts_actual_document(self):
        original='Первая строка.\nЦелевой процент роста 77% без базовых данных.'
        key=next(k for k,v in _evidence_chunks(original,'Календарь') .items() if '77%' in v)
        issue={'pass':False,'issues':[{'document':'strategy','evidence_id':key,
            'type':'invented_fact','reason':'Без исходных данных указано неверифицируемое обещание.'}]}
        out=parse_verdict(json.dumps(issue,ensure_ascii=False),strategy=original,calendar='Календарь')
        self.assertIn('77%',out['issues'][0]['quote'])
        self.assertIn(key,_numbered_document('strategy',original,'Календарь'))

    def test_invalid_evidence_ids_cannot_launder_claims(self):
        source='Ненадёжное обещание без доказательств.'
        for ref in ('strategy:9999','calendar:0001','strategy:0',None,99):
            issue={'pass':False,'issues':[{'document':'strategy','evidence_id':ref,
                'type':'unsafe_claim','reason':'Отсутствует подтверждение обещания.'}]}
            with self.subTest(ref=ref),self.assertRaises(ValueError):
                parse_verdict(json.dumps(issue,ensure_ascii=False),strategy=source,calendar='План')

    def test_unanchored_model_quote_with_valid_id_uses_host_evidence(self):
        issue={'pass':False,'issues':[{'document':'strategy','evidence_id':'strategy:0001',
            'quote':'фантастическая цитата','type':'quality','reason':'Детально описанная ошибка.'}]}
        result=parse_verdict(json.dumps(issue,ensure_ascii=False),strategy='Реальный текст.',calendar='План')
        self.assertEqual(result['issues'][0]['quote'],'Реальный текст.')

    def test_model_returns_id_and_host_checks_real_document(self):
        data={'pass':False,'issues':[{'document':'strategy','evidence_id':'strategy:0001',
                'type':'invented_fact','claim':'Адрес: проспект Придуманный, 72.','reason':'Адрес не подтверждён исходными данными.'
                }]}
        def fake_urlopen(req,timeout):
            body=json.loads(req.data)
            self.assertIn('[strategy:0001]',body['messages'][1]['content'])
            return io.BytesIO(json.dumps({'message':{'content':json.dumps(assessed_reply(body,data))}}).encode())
        with patch('independent_acceptance_editor.urllib.request.urlopen',side_effect=fake_urlopen):
            out=call_editor('Адрес неизвестен','Адрес: проспект Придуманный, 72.','План есть.')
        self.assertFalse(out['pass'])
        self.assertIn('Адрес:',out['issues'][0]['quote'])


class ChunkCoverageTests(unittest.TestCase):
    def test_every_calendar_character_reviewed(self):
        strategy='Стратегия малого бизнеса.'
        calendar='\n'.join('Пост %d: '+('Текст '+str(i)+' ')*25 for i in range(400))
        windows=_review_windows('Сделай качественный контент-план.',strategy,calendar)
        self.assertGreater(len(windows),1)
        self.assertEqual(''.join(x[1] for x in windows),calendar)
        self.assertTrue(all(len(a)+len(b)+len('Сделай качественный контент-план.')<=EDITOR_CHARACTER_BUDGET for a,b in windows))

    def test_no_excessive_document_silently_accepted(self):
        with self.assertRaises(ValueError):
            _review_windows('Исходные ограничения '*10000,'Стратегия','Календарь')

    def test_split_preserves_exact_unicode(self):
        text=('Привет 🪴\n'*6000)
        self.assertEqual(''.join(_split_document(text,4700)),text)

    def test_chunk_failure_is_not_overwritten_by_subsequent_pass(self):
        strat='Стратегия для компании.'
        calendar='В первой половине неверное обещание.\n'+'Публикация с форматом, KPI и CTA.\n'*950
        seen=[]
        def fake_urlopen(req,timeout):
            body=json.loads(req.data)
            user=body['messages'][1]['content']
            seen.append(user)
            if 'В первой половине' in user:
                obj={'pass':False,'issues':[{'document':'calendar','evidence_id':'calendar:0001',
                    'type':'unsafe_claim','claim':'В первой половине неверное обещание.',
                    'reason':'Это недостоверная абсолютная гарантия.'}]}
            else:
                obj={'pass':True,'issues':[]}
            return io.BytesIO(json.dumps({'message':{'content':json.dumps(obj,ensure_ascii=False)}},ensure_ascii=False).encode())
        with patch('independent_acceptance_editor.urllib.request.urlopen',side_effect=fake_urlopen):
            verdict=call_editor('Пользователь просит не обещать гарантий.',strat,calendar,
                editor_call=__import__('independent_acceptance_editor')._call_editor_once)
        self.assertGreater(len(seen),1)
        self.assertFalse(verdict['pass'])
        self.assertTrue(verdict['issues'])


if __name__=='__main__':unittest.main()
