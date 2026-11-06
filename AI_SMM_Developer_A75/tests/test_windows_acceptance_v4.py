from substance_fixture import assessed_reply
"""V4 regression: independent editor, cross-document contracts, bounded recovery."""
import io
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from independent_acceptance_editor import parse_verdict, call_editor, _ground_quote
from compound_retry import run_compound_with_retry
from strategy_integrity import strategy_integrity_issues


class EditorEvidenceTests(unittest.TestCase):
    STRATEGY='Стратегия: количество заявок измеряем по сообщениям в DM.\nЦена неизвестна.'
    CALENDAR='Календарь: 2 публикации.\nCTA: Напишите нам в DM.'

    def test_literal_evidence_accepted(self):
        d={'pass':False,'issues':[{'document':'strategy','type':'quality',
                'quote':'Цена неизвестна.','reason':'Описан неизвестный показатель.'}]}
        result=parse_verdict(json.dumps(d,ensure_ascii=False),strategy=self.STRATEGY,calendar=self.CALENDAR)
        self.assertEqual(result['issues'][0]['quote'],'Цена неизвестна.')

    def test_whitespace_normalized_remains_source_grounded(self):
        self.assertEqual(_ground_quote('abc\n  def  ghi','abc def ghi'),'abc\n  def  ghi')

    def test_unanchored_quote_is_never_accepted(self):
        d={'pass':False,'issues':[{'document':'strategy','type':'quality',
                'quote':'Неверная цитата','reason':'Подробное объяснение ошибки.'}]}
        with self.assertRaisesRegex(ValueError,'отсутствует в документе'):
            parse_verdict(json.dumps(d,ensure_ascii=False),strategy=self.STRATEGY,calendar=self.CALENDAR)

    def test_pass_with_errors_is_blocked(self):
        d={'pass':True,'issues':[{'document':'calendar','type':'quality',
                'quote':'2 публикации','reason':'Существует недоработка.'}]}
        self.assertFalse(parse_verdict(json.dumps(d,ensure_ascii=False),strategy=self.STRATEGY,calendar=self.CALENDAR)['pass'])

    def test_host_only_editor_corrects_invalid_reference_on_bounded_retry(self):
        bad={'pass':False,'issues':[{'document':'strategy','type':'quality',
                'evidence_id':'strategy:9999','claim':'Количество заявок измеряем по сообщениям в DM.','reason':'Некорректный показатель.'}]}
        good={'pass':False,'issues':[{'document':'strategy','type':'quality',
                'evidence_id':'strategy:0002','claim':'Цена неизвестна.','reason':'Отсутствует подтверждение цены.'}]}
        answers=[bad,good]
        sent=[]
        def fake_urlopen(req, timeout):
            sent.append(json.loads(req.data.decode('utf-8')))
            return io.BytesIO(json.dumps({'message':{'content':json.dumps(assessed_reply(sent[-1],answers.pop(0)),ensure_ascii=False)}},
                                       ensure_ascii=False).encode('utf-8'))
        with patch('independent_acceptance_editor.urllib.request.urlopen',side_effect=fake_urlopen):
            result=call_editor('Запрос',self.STRATEGY,self.CALENDAR)
        self.assertFalse(result['pass'])
        self.assertEqual(result['issues'][0]['quote'],'Цена неизвестна.')
        self.assertEqual(len(sent),2)
        self.assertEqual(len(sent[1]['messages']),4)

    def test_model_supplied_claim_is_ignored_in_favor_of_host_evidence_id(self):
        bad={'pass':False,'issues':[{'document':'strategy','type':'quality',
                'evidence_id':'strategy:0001','claim':'Несуществующая цитата','reason':'Отсутствуют данные.'}]}
        calls=[]
        def fake_urlopen(req,timeout):
            calls.append(req)
            return io.BytesIO(json.dumps({'message':{'content':json.dumps(assessed_reply(json.loads(req.data),bad),ensure_ascii=False)}},
                                       ensure_ascii=False).encode('utf-8'))
        with patch('independent_acceptance_editor.urllib.request.urlopen',side_effect=fake_urlopen):
            result=call_editor('Запрос',self.STRATEGY,self.CALENDAR)
        self.assertFalse(result['pass'])
        self.assertEqual(result['issues'][0]['quote'],self.STRATEGY.splitlines()[0])
        self.assertNotEqual(result['issues'][0]['quote'],'Несуществующая цитата')
        self.assertEqual(len(calls),1)


class StrategyContractTests(unittest.TestCase):
    def test_detects_cadence_conflict(self):
        found=strategy_integrity_issues('Публикация по 1 публикации в неделю.','Нужно по 2 публикации в неделю.',per_week=2)
        self.assertTrue(any('частоте' in e for e in found))

    def test_does_not_fail_correct_cadence(self):
        self.assertFalse(strategy_integrity_issues('Публикация по 2 публикации в неделю.', '2 в неделю',per_week=2))

    def test_unverified_numeric_kpi_is_not_marked_verified(self):
        found=strategy_integrity_issues('Ориентир: Увеличение подписчиков на 10% за неделю.',
                    'Нет аналитики аккаунта.',per_week=2)
        self.assertTrue(any('процент' in x for x in found))

    def test_hypothetical_kpi_is_allowed(self):
        found=strategy_integrity_issues('Гипотеза: увеличение подписчиков на 10% за неделю после замера базы.',
                    'Нет аналитики аккаунта.',per_week=2)
        self.assertEqual(found,[])

    def test_user_requested_kpi_is_not_rejected(self):
        found=strategy_integrity_issues('Цель: увеличить запросы на 10% за неделю.',
                    'Поставь цель увеличить запросы на 10%.',per_week=2)
        self.assertEqual(found,[])


class TaskRecoveryTests(unittest.TestCase):
    @staticmethod
    def document(kind,valid=True,saved=False):
        return {'kind':kind,'valid':valid,'content':'полный документ','already_saved':saved,
                'reason':'' if valid else 'Некорректный результат'}

    @classmethod
    def result(cls,complete):
        docs=[cls.document('strategy'),cls.document('calendar',valid=complete)]
        delivery={'unfulfilled':[],'rendered_media':'not_requested',
                  'deliverables':{'strategy':'validated','calendar':'validated'},
                  'scope':{'weeks':1,'per_week':2,'planned_total':2},'checked_posts':2,
                  'content_components_checked':{k:2 for k in ('caption','visual','cta','kpi','audience','measurement','materials')},
                  'plan_preflight':{'reviewer_available':True,'unresolved_slots':[]},
                  'task_orchestration':{'completed':True}}
        return {'action':'compound_documents','documents':docs,'content_delivery':delivery}

    def test_successful_execution_does_not_retry(self):
        seen=[]
        def execute(request,ctx,progress=None):
            seen.append(ctx.copy());return self.result(True)
        final=run_compound_with_retry(execute,'исходный запрос',{'task_mode':'new'})
        self.assertEqual(len(seen),1)
        self.assertTrue(final['documents'][1]['valid'])

    def test_partial_execution_is_retried_with_same_request_and_unsaved_strategy(self):
        seen=[]
        def execute(request,ctx,progress=None):
            seen.append((request,ctx.copy()))
            return self.result(len(seen)>1)
        events=[]
        final=run_compound_with_retry(execute,'исходный запрос',{'project_workspace':'sandbox','task_mode':'new'},progress=events.append)
        self.assertEqual(len(seen),2)
        self.assertEqual([x[0] for x in seen],['исходный запрос']*2)
        self.assertEqual(seen[1][1]['task_mode'],'resume')
        self.assertFalse(seen[1][1]['resume_documents'][0]['already_saved'])
        self.assertEqual(events[-1]['stage'],'autonomous_retry')
        self.assertTrue(final['documents'][1]['valid'])

    def test_persisted_strategy_receipt_survives_retry(self):
        seen=[]
        def execute(request,ctx,progress=None):
            seen.append(ctx.copy())
            result=self.result(False)
            result['documents'][0]['already_saved']=True
            return result
        run_compound_with_retry(execute,'оригинал',{'task_mode':'resume'},max_attempts=2)
        self.assertTrue(seen[1]['resume_documents'][0]['already_saved'])

    def test_failed_attempts_remain_failed(self):
        seen=[]
        def execute(request,ctx,progress=None):
            seen.append(ctx.copy());return self.result(False)
        final=run_compound_with_retry(execute,'оригинал',{'task_mode':'new'},max_attempts=2)
        self.assertEqual(len(seen),2)
        self.assertFalse(final['documents'][1]['valid'])

    def test_invalid_retry_budget_rejected(self):
        with self.assertRaises(ValueError):
            run_compound_with_retry(lambda *a,**k: None, 'x', {},max_attempts=99)

    def test_source_has_gui_integration_and_no_false_saved_flag(self):
        base=Path(__file__).resolve().parent
        if not (base/'app.py').is_file():
            base=base.parent
        app=(base/'app.py').read_text(encoding='utf-8')
        engine=(base/'compound_tasks.py').read_text(encoding='utf-8')
        self.assertIn('run_text_task(',app)
        self.assertIn("'already_saved':True",(base/'dialogue_execution.py').read_text(encoding='utf-8'))
        self.assertIn("d.get('already_saved') is True",engine)


if __name__=='__main__': unittest.main()

class FinalEditorialGateTests(unittest.TestCase):
    @staticmethod
    def prepared():
        from test_windows_acceptance_v4 import TaskRecoveryTests
        obj=TaskRecoveryTests.result(True)
        obj['documents'][0]['content']='Стратегия для проекта без подтверждённого адреса.'
        obj['documents'][1]['content']='Календарь для проекта. Место встречи не определено.'
        return obj

    def test_pass_annotation_does_not_change_original(self):
        from independent_quality_gate import final_quality_gate
        original=self.prepared()
        result=final_quality_gate(original,'Запрос',lambda *args: {'pass':True,'issues':[]})
        self.assertIsNot(original,result)
        self.assertTrue(result['documents'][1]['valid'])
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'PASS')
        self.assertNotIn('independent_editor',original['content_delivery'])

    def test_evidence_grounded_criticism_blocks_calendar_not_strategy(self):
        from independent_quality_gate import final_quality_gate
        original=self.prepared()
        note={'document':'calendar','type':'invented_fact',
              'quote':'Место встречи не определено.','reason':'Данные об адресе отсутствуют.'}
        result=final_quality_gate(original,'Запрос',lambda *args: {'pass':False,'issues':[note]})
        self.assertTrue(result['documents'][0]['valid'])
        self.assertFalse(result['documents'][1]['valid'])
        self.assertFalse(result['content_delivery']['task_orchestration']['completed'])
        self.assertEqual(result['content_delivery']['independent_editor']['issues'][0]['quote'],note['quote'])
        self.assertTrue(original['documents'][1]['valid'])

    def test_unverifiable_evidence_never_counts_as_pass(self):
        from independent_quality_gate import final_quality_gate
        original=self.prepared()
        note={'document':'calendar','type':'invented_fact',
              'quote':'Фраза отсутствует','reason':'Нет данных о компании.'}
        result=final_quality_gate(original,'Запрос',lambda *args: {'pass':False,'issues':[note]})
        self.assertFalse(result['documents'][1]['valid'])
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'BLOCKED')

    def test_editor_unavailable_blocks_output_without_crashing(self):
        from independent_quality_gate import final_quality_gate
        def crash(*args):raise RuntimeError('model offline')
        result=final_quality_gate(self.prepared(),'Запрос',crash)
        self.assertFalse(result['documents'][1]['valid'])
        self.assertIn('model offline',result['documents'][1]['reason'])

    def test_partial_task_never_runs_editor(self):
        from independent_quality_gate import final_quality_gate
        incomplete=self.prepared()
        incomplete['documents'][1]['valid']=False
        result=final_quality_gate(incomplete,'Запрос',lambda *args: self.fail('should not call editor'))
        self.assertIs(result,incomplete)

class MandatoryIndependentEditorialReceiptTests(unittest.TestCase):
    def test_gui_completion_gate_needs_editor_receipt(self):
        from completion_gate import can_close_task
        from test_windows_acceptance_v4 import TaskRecoveryTests
        report=TaskRecoveryTests.result(True)['content_delivery']
        self.assertFalse(can_close_task({'strategy','calendar'},report))
        report['independent_editor']={'status':'BLOCKED','issues':[]}
        self.assertFalse(can_close_task({'strategy','calendar'},report))
        report['independent_editor']={'status':'PASS','issues':['unexpected issue']}
        self.assertFalse(can_close_task({'strategy','calendar'},report))
        report['independent_editor']={'status':'PASS','issues':[]}
        self.assertTrue(can_close_task({'strategy','calendar'},report))

    def test_internal_retry_precondition_may_skip_final_editor_but_not_gui_commit(self):
        from completion_gate import can_close_task
        from test_windows_acceptance_v4 import TaskRecoveryTests
        report=TaskRecoveryTests.result(True)['content_delivery']
        self.assertTrue(can_close_task({'strategy','calendar'},report,require_editor=False))
        self.assertFalse(can_close_task({'strategy','calendar'},report))
