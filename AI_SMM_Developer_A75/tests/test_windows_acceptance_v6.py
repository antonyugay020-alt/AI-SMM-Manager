from substance_fixture import assessed_reply
# -*- coding: utf-8 -*-
"""V6: production boundaries on Windows findings, not scripted happy paths."""
import io
import json
import unittest
from unittest.mock import patch

from strategy_recovery import produce_checked_strategy
from independent_acceptance_editor import call_editor, parse_verdict, _evidence_chunks
from windows_acceptance import format_process_log


class AcceptanceDiagnosticsTests(unittest.TestCase):
    def test_child_logs_keep_failure_details_beyond_old_tail_limit(self):
        marker='SPECIFIC_FAILURE_ASSERTION_314'
        log=format_process_log(1,'x'*20000+marker,'traceback detail')
        self.assertIn(marker,log)
        self.assertIn('traceback detail',log)
        self.assertIn('returncode: 1',log)
        self.assertIn('stdout_bytes:',log)


class StrategyRecoveryTests(unittest.TestCase):
    def test_wrong_explicit_cadence_is_canonicalized_without_model_retry(self):
        req = 'Две публикации в неделю. У нас нет начальной статистики.'
        candidates = ['Планируем по 1 публикации в неделю. KPI измеряется после замера базы.']
        calls = []
        events = []
        def generate(feedback, attempt):
            calls.append((feedback, attempt))
            return candidates.pop(0)
        def validator(text, value):
            return True, ''
        result = produce_checked_strategy(req, 2, generate, validator,
                                           report=lambda name,**kwargs: events.append(name))
        self.assertTrue(result[1])
        self.assertEqual(len(calls), 1)
        self.assertIn('по 2 публикации в неделю',result[0])
        self.assertIn('strategy_cadence_normalized',events)

    def test_explicit_cadence_normalization_is_deterministic_and_bounded(self):
        count=[]
        def generate(feedback, attempt):
            count.append(attempt)
            return 'по 1 публикации в неделю'
        item=produce_checked_strategy('по 2 публикации в неделю', 2,generate,
                                      lambda req,text:(True,''),attempts=3)
        self.assertTrue(item[1]); self.assertEqual(count,[1])
        self.assertEqual(item[0],'по 2 публикации в неделю')

    def test_saved_strategy_not_mutated_even_when_invalid(self):
        def illegal(*args): self.fail('Generation may not rewrite an existing strategy')
        old='по 1 публикации в неделю'
        item=produce_checked_strategy('по 2 публикации в неделю',2,illegal,
                                      lambda req,text:(True,''),existing=old)
        self.assertEqual(item[0],old);self.assertFalse(item[1])

    def test_validator_and_invalid_model_response_are_not_silent_pass(self):
        for answer in ('',None,'A' * 7):
            with self.subTest(answer=answer):
                item=produce_checked_strategy('Контент-план',3,lambda f,a:answer,
                    lambda req,text:(len(text)>100,'Недостаточно подробный документ'),attempts=1)
                self.assertFalse(item[1])

    def test_bounded_attempts_are_explicit(self):
        for n in (0,-1,5,'three',True):
            with self.subTest(n=n),self.assertRaises(ValueError):
                produce_checked_strategy('Задание',3,lambda f,a:'x',lambda r,s:(True,''),attempts=n)


class ConstrainedEvidenceTests(unittest.TestCase):
    def test_ollama_receives_schema_with_only_real_refs(self):
        strategy='Адрес компании неизвестен.'
        calendar='Публикация с неподтверждённым адресом.'
        refs=set(_evidence_chunks(strategy,calendar,table_cells=True))
        seen=[]
        def fake_urlopen(request,timeout):
            payload=json.loads(request.data)
            schema=payload['format']
            self.assertIsInstance(schema,dict)
            self.assertEqual(set(schema['properties']['issues']['items']['properties']['evidence_id']['enum']),refs)
            required=schema['properties']['issues']['items']['required']
            self.assertEqual(required,['document','type','evidence_id','reason','criterion_id'])
            self.assertNotIn('claim',schema['properties']['issues']['items']['properties'])
            seen.append(payload)
            answer={'pass':False,'issues':[{'document':'calendar','type':'invented_fact',
                'evidence_id':'calendar:0001','claim':'Публикация с неподтверждённым адресом.',
                'reason':'Этот адрес отсутствует в исходных сведениях.'}]}
            return io.BytesIO(json.dumps({'message':{'content':json.dumps(assessed_reply(payload,answer),ensure_ascii=False)}},ensure_ascii=False).encode())
        with patch('independent_acceptance_editor.urllib.request.urlopen',side_effect=fake_urlopen):
            opinion=call_editor('Не придумывай адрес.',strategy,calendar)
        self.assertEqual(len(seen),1)
        self.assertFalse(opinion['pass'])
        self.assertIn('адресом',opinion['issues'][0]['quote'])

    def test_grammar_cannot_override_host_invalid_evidence(self):
        strategy='Факты отсутствуют'
        calendar='Календарь'
        with self.assertRaises(ValueError):
            parse_verdict(json.dumps({'pass':False,'issues':[{
                'document':'strategy','type':'invented_fact','evidence_id':'strategy:9999',
                'reason':'Недостоверная ссылка на несуществующее доказательство'}]}),
                strategy=strategy,calendar=calendar)

    def test_persistent_malformed_editor_still_blocks(self):
        calls=[]
        def fake_urlopen(req, timeout):
            calls.append(json.loads(req.data))
            data={'pass':False,'issues':[{'type':'other','reason':'Ошибка есть'}]}
            return io.BytesIO(json.dumps({'message':{'content':json.dumps(data)}}).encode())
        with patch('independent_acceptance_editor.urllib.request.urlopen',side_effect=fake_urlopen):
            result=call_editor('Нужна стратегия','Стратегия','Календарь')
        self.assertEqual(len(calls),2)
        self.assertFalse(result['pass'])
        self.assertEqual(result['review_state'],'unavailable')
        self.assertTrue(any(x.get('type')=='review_unavailable' for x in result['issues']))

    def test_no_problems_still_requires_structural_editor_verdict(self):
        def fake_urlopen(req,timeout):
            return io.BytesIO(json.dumps({'message':{'content':json.dumps(assessed_reply(json.loads(req.data),{'pass':True,'issues':[]}))}}).encode())
        with patch('independent_acceptance_editor.urllib.request.urlopen',side_effect=fake_urlopen):
            opinion=call_editor('Пиши конкретно','Стратегия','Календарь')
        self.assertTrue(opinion['pass'])


if __name__ == '__main__': unittest.main()

class ProductionConnectionTests(unittest.TestCase):
    def test_actual_compound_entry_normalizes_wrong_explicit_cadence_before_planning(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from compound_tasks import execute_compound
        replies=['Планируем по 1 публикации в неделю.']
        stages=[]
        observed=[]
        def fake_ask(model,system,user,*,stage='?',report=None):
            if 'стратегии' in stage:
                observed.append((stage,user))
                return replies.pop(0)
            raise AssertionError('No real Ollama call expected: '+stage)
        request='Подготовь SMM-стратегию и контент-план на 1 неделю по 2 публикации в неделю, всего 2 публикации.'
        with TemporaryDirectory(prefix='qa_strategy_v6_') as temp, \
             patch('compound_tasks.analyze',return_value={'business':'Тестовый проект','channels':'Instagram','channels_basis':'recommendation'}), \
             patch('compound_tasks._ask',side_effect=fake_ask), \
             patch('compound_tasks.validate_document',return_value=(True,'')), \
             patch('compound_tasks.plan_slots',return_value=[]), \
             patch('compound_tasks.govern_plan',return_value=([],{},True)):
            context={'project':'test','project_workspace':str(Path(temp)/'project'),
                     'category':'тест','description':request}
            result=execute_compound(request,context,progress=stages.append)
        docs=result.get('documents',[])
        strategies=[d for d in docs if d.get('kind')=='strategy']
        self.assertEqual(len(strategies),1)
        self.assertTrue(strategies[0]['valid'])
        self.assertIn('по 2 публикации',strategies[0]['content'])
        self.assertEqual(len(observed),1)
        self.assertTrue(any(e.get('stage')=='strategy_cadence_normalized' for e in stages))


class WindowsPytestSummaryTests(unittest.TestCase):
    """Real 2026-11-02 Windows report showed four false failures, all exit 0."""
    def test_real_windows_shard_summaries_are_pass(self):
        from windows_acceptance import parse_pytest_completion
        observed = [
            ('512 passed, 2 skipped, 240 subtests passed in 29.48s', 512, 2, 240),
            ('360 passed, 13 skipped, 32 subtests passed in 30.16s', 360, 13, 32),
            ('373 passed, 14 skipped, 341 subtests passed in 20.06s', 373, 14, 341),
            ('410 passed, 5 skipped, 195 subtests passed in 71.32s (0:01:11)', 410, 5, 195),
        ]
        for line, passed, skipped, subtests in observed:
            with self.subTest(line=line):
                result=parse_pytest_completion('... [100%]\n' + line, 0)
                self.assertTrue(result['success'])
                self.assertEqual((result['passed'],result['skipped'],result['subtests']),
                                 (passed,skipped,subtests))

    def test_existing_simple_summary_still_accepted(self):
        from windows_acceptance import parse_pytest_completion
        self.assertEqual(parse_pytest_completion('22 passed in 0.32s', 0),
                         {'success': True, 'passed': 22, 'skipped': 0, 'subtests': 0})

    def test_failures_and_incomplete_output_block(self):
        from windows_acceptance import parse_pytest_completion
        cases = [
            ('512 passed, 2 skipped, 240 subtests passed in 29.48s', 2),
            ('... [100%]\n', 0),
            ('10 passed, 1 failed in 1.02s', 0),
            ('0 passed, 10 skipped in 0.21s', 0),
            ('10 passed, 1 error in 0.21s', 0),
            ('10 passed, 1 xpassed in 0.21s', 0),
            ('10 passed', 0),
        ]
        for line, code in cases:
            with self.subTest(line=line,code=code):
                self.assertFalse(parse_pytest_completion(line,code)['success'])
