"""Regression contracts derived from the real Windows A2 failures."""
import json
import tempfile
import unittest
from document_recovery import DocumentDraft, patch_document
from strategy_recovery import produce_checked_strategy
from semantic_brief import plan_slots
from plan_governance import govern_plan
from calendar_planner import formats_for_platform

class PatchProtocolTests(unittest.TestCase):
    def test_schema_only_offers_editable_ids_and_sends_all_context_once(self):
        body='# Header\n- По 1 публикации в неделю\n'
        def ask(model,system,user,**kw):
            payload=json.loads(user)
            self.assertEqual(payload['protected_lines']['L00001'],'# Header')
            self.assertEqual(kw['json_schema']['properties']['changes']['items']['properties']['line_id']['enum'],['L00002'])
            return json.dumps({'changes':[{'line_id':'L00002','text':'- По 2 публикации в неделю'}]})
        result=patch_document(ask,'m','по 2 публикации в неделю',body,'Неверная частота',constrained=True)
        self.assertEqual(result,'# Header\n- По 2 публикации в неделю\n')

    def test_unchanged_heading_is_not_a_structure_change(self):
        def ask(*a):return json.dumps({'changes':[{'line_id':'L00001','text':'# Heading'},
                                                 {'line_id':'L00002','text':'new'}]})
        self.assertEqual(patch_document(ask,'m','r','# Heading\nold','reason'),'# Heading\nnew')

    def test_blank_replacement_cannot_delete_kpi(self):
        with self.assertRaisesRegex(ValueError,'erase'):
            patch_document(lambda *a:'{"changes":[{"line_id":"L00002","text":""}]}',
                           'm','r','# KPI\nБаза: 1000 просмотров','База неизвестна')

    def test_patch_failure_is_feedback_for_next_attempt_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            store=DocumentDraft(root,'signature','strategy')
            bad='Рост аудитории удвоится за неделю.'
            store.save(bad,False,'Увеличение без исходной базы')
            feedback=[]
            def repair(body,reason,attempt):
                feedback.append(reason)
                if len(feedback)==1:raise ValueError('Patch made no useful change')
                self.assertIn('Patch made no useful change',reason)
                self.assertIn('Увеличение без исходной базы',reason)
                self.assertIn('Patch made no useful change',store.load()['reason'])
                return 'Измерьте базу за месяц и затем задайте гипотезу.'
            def validator(request,text):
                return ('удвоится' not in text,'Осталось обещание роста' if 'удвоится' in text else '')
            def generate(*args):self.fail('A persisted rejection must stay on the targeted-repair path')
            result=produce_checked_strategy('По 2 публикации в неделю',2,generate,validator,
                repair=repair,draft=store.load(),checkpoint=store.save)
            self.assertTrue(result[1]);self.assertEqual(len(feedback),2)
            self.assertIn(bad,store.load()['history'][0]['content'])

    def test_repeated_failure_retains_business_defect_in_final_reason(self):
        def repair(*a):raise ValueError('No change')
        result=produce_checked_strategy('Подготовь стратегию',2,
            lambda *a:self.fail('A persisted rejection must not be regenerated'),
            lambda *a:(False,'Unsupported business claim'),
            repair=repair,draft={'content':'Рост удвоится','reason':'Unsupported business claim'},attempts=2)
        self.assertFalse(result[1])
        self.assertIn('Unsupported business claim',result[2]);self.assertIn('No change',result[2])

class PlatformPlanningTests(unittest.TestCase):
    def test_telegram_format_is_assigned_by_host_despite_incompatible_model_label(self):
        calls=[]
        def ask(m,s,u):
            data=json.loads(u);calls.append(data)
            return json.dumps({'posts':[{'topic':'Как задать вопрос о занятиях','format':'Reels'}]})
        result=plan_slots(ask,'m','Telegram, одна публикация','Стратегия',{'channels':['Telegram']},1,per_week=1)
        self.assertEqual(len(calls),1)
        self.assertEqual(result[0]['format'],formats_for_platform('Telegram')[0])
        self.assertEqual(result[0]['platform'],'Telegram')

    def test_accepted_incompatible_plan_is_blocked_and_never_rewritten(self):
        slot={'week':1,'day':'Пн','topic':'Как подготовиться к занятию',
              'brief':'Показать подтверждённый процесс','goal':'Польза','audience':'Новички',
              'format':'Reels','platform':'Telegram'}
        def forbidden(*a):self.fail('Malformed accepted plan must not be rewritten')
        result,issues,available=govern_plan(forbidden,'m','Задание',{},'Стратегия',[slot],{'1/Пн'},expected_ids=['1/Пн'])
        self.assertEqual(result,[slot]);self.assertIn('1/Пн',issues);self.assertFalse(available)
