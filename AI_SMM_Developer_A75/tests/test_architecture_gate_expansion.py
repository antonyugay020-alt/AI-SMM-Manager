"""Adversarial, industry-neutral lifecycle and plan-contract regressions."""
import unittest
from content_requirements import PublicationScope, delivery_audit
from plan_governance import govern_plan, structural_issues
from publication_schedule import publication_ids


def complete_post(ident):
    return {"id":ident,"topic":"Конкретная неповторяющаяся тема "+ident,
            "caption":"Подробная публикация " + ident, "visual":"Визуальный сценарий", "cta":"Ответьте на вопрос",
            "kpi":"Обращения", "audience":"Читатели", "measurement":"Отслеживать обращения",
            "materials":"Понадобятся кадры"}


class AuditNegativeContracts(unittest.TestCase):
    def test_wrong_days_cannot_pass_by_totals_alone(self):
        scope=PublicationScope(2,2,4)
        posts={ident:complete_post(ident) for ident in ('1/Пн','1/Вт','2/Пн','2/Вт')}
        docs=[{"kind":"strategy","valid":True,"content":"strategy"}, {"kind":"calendar","valid":True,"content":"calendar"}]
        self.assertTrue(delivery_audit(docs,scope,posts), 'Invalid canonical slots were approved')

    def test_unknown_and_duplicate_plan_slots_fail_closed(self):
        required=['1/Вт','1/Пт']
        valid=[{'week':1,'day':'Вт','format':'Reels','topic':'Тема запуска для пользователей'},
               {'week':1,'day':'Пт','format':'Reels','topic':'Тема подсказок для клиентов'}]
        for extra in ({'week':2,'day':'Ср','format':'Reels','topic':'Лишняя тема за пределами задания'},
                      {'week':1,'day':'Вт','format':'Reels','topic':'Дублированный слот'},
                      {'week':[], 'day':'Нд', 'topic':'Плохой тип недели'}):
            with self.subTest(extra=extra):
                plan=valid+[extra]
                issues=structural_issues(plan,required)
                self.assertTrue(issues, 'Unrequested plan slot was silently accepted')
                revised,blocked,available=govern_plan(lambda *_:'{"issues":[]}', 'model','Brief',{},'',plan,set(),expected_ids=required)
                self.assertFalse(available and not blocked,'Malformed plan cannot be approved')

    def test_incomplete_plan_fields_are_not_silently_approved(self):
        required=['1/Вт']
        for bad in ({'week':1,'day':'Вт','topic':'Тема без рабочего формата'},
                    {'week':True,'day':'Вт','format':'Reels','topic':'Булева неделя'},
                    {'week':1,'day':'Вт','format':'Reels','topic':123},
                    {'week':1,'day':'Вт','format':'Reels','topic':'Тема','brief':[]},
                    {'week':1,'day':'Вт','format':'Reels','topic':'Тема', 'brief':'План','goal':None}):
            with self.subTest(bad=bad):
                _,blocked,available=govern_plan(lambda *_:'{"issues":[]}', 'model','Brief',{},'', [bad],set(),expected_ids=required)
                self.assertTrue(blocked or not available)

    def test_complete_plan_without_expected_ids_does_not_invent_approval(self):
        slots=[{'week':1,'day':'Вт','topic':'Тема очень конкретная','format':'Reels'}]
        _,blocked,available=govern_plan(lambda *_:'{"issues":[]}', 'model','Brief',{},'',slots,set(),expected_ids=['1/Вт','1/Пт'])
        self.assertIn('1/Пт',blocked)

if __name__=='__main__':unittest.main()

class PersistenceCompletionBoundaryTests(unittest.TestCase):
    def sample(self):
        return {'scope':{'weeks':1,'per_week':2,'planned_total':2},
                'deliverables':{'strategy':'validated','calendar':'validated'},
                'checked_posts':2,'content_components_checked':{key:2 for key in
                  ('caption','visual','cta','kpi','audience','measurement','materials')},
                'rendered_media':'not_requested','unfulfilled':[],
                'plan_preflight':{'reviewer_available':True,'unresolved_slots':[]},
                'task_orchestration':{'completed':True},'independent_editor':{'status':'PASS','issues':[]}}

    def test_completion_requires_all_evidence_and_real_storage(self):
        from completion_gate import can_close_task
        baseline=self.sample()
        self.assertTrue(can_close_task({'strategy','calendar'},baseline))
        self.assertFalse(can_close_task({'strategy'},baseline))
        self.assertFalse(can_close_task({'strategy','calendar'},{}))
        for section in ('scope','deliverables','content_components_checked','plan_preflight','task_orchestration'):
            with self.subTest(section=section):
                broken=self.sample();broken.pop(section)
                self.assertFalse(can_close_task({'strategy','calendar'},broken))
        for field,value in [('checked_posts',1),('unfulfilled',['missing']),('rendered_media','not_created')]:
            with self.subTest(field=field):
                broken=self.sample();broken[field]=value
                self.assertFalse(can_close_task({'strategy','calendar'},broken))
        broken=self.sample();broken['content_components_checked']['visual']=1
        self.assertFalse(can_close_task({'strategy','calendar'},broken))
        broken=self.sample();broken['plan_preflight']['reviewer_available']=False
        self.assertFalse(can_close_task({'strategy','calendar'},broken))
        broken=self.sample();broken['task_orchestration']['completed']=False
        self.assertFalse(can_close_task({'strategy','calendar'},broken))

class TaskLedgerAtomicityTests(unittest.TestCase):
    def test_one_corrupt_entry_does_not_discard_later_valid_states(self):
        import tempfile,json
        from pathlib import Path
        from task_orchestration import TaskLedger
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'task_orchestration_sample.json'
            path.write_text(json.dumps({'signature':'sample','outputs':{
                'a':{'kind':'artifact','status':'accepted','revisions':1},
                'bad':{'kind':'artifact','status':'blocked','revisions':[]},
                'c':{'kind':'artifact','status':'blocked','revisions':2,'reason':'error'}},
                'completed':True}),encoding='utf8')
            ledger=TaskLedger.open(td,'sample')
            self.assertEqual(set(ledger.outputs),{'a','c'})
            self.assertFalse(ledger.completed)

    def test_failed_persist_cannot_modify_in_memory_approval(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from task_orchestration import TaskLedger
        with tempfile.TemporaryDirectory() as td:
            ledger=TaskLedger.open(Path(td),'atomic')
            ledger.register('document',['strategy','calendar'])
            before={k:dict(v) for k,v in ledger.outputs.items()}
            with patch.object(ledger,'persist',side_effect=OSError('simulated disk full')):
                with self.assertRaises(OSError):ledger.set_status('strategy','accepted')
            self.assertEqual(ledger.outputs,before)
            with patch.object(ledger,'persist',side_effect=OSError('simulated disk full')):
                with self.assertRaises(OSError):ledger.register('new',['extra'])
            self.assertEqual(ledger.outputs,before)
            with patch.object(ledger,'persist',side_effect=OSError('simulated disk full')):
                with self.assertRaises(OSError):ledger.reconcile(['strategy'],strategy=True)
            self.assertEqual(ledger.outputs,before)
            self.assertFalse(ledger.completed)

class FullPipelineShapeAndScopeTests(unittest.TestCase):
    def test_corrupt_model_plan_never_reaches_writer(self):
        import tempfile
        from unittest.mock import patch
        import compound_tasks as ct
        from plan_review_fixture import with_plan_review
        request='Разработай стратегию и контент-план для студии ремонта на 1 неделю, 2 публикации в неделю'
        with tempfile.TemporaryDirectory() as td:
            for plan in (None, [None], [{'week':1,'day':[],'topic':'Некорректная тема'}],
                         [{'week':2,'day':'Ср','topic':'Лишняя тема'}]):
                with self.subTest(plan=plan), patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                     patch.object(ct,'_ask',side_effect=with_plan_review('Цели, KPI, аудитория, каналы. '*90)), \
                     patch.object(ct,'validate_document',return_value=(True,'')), \
                     patch.object(ct,'plan_slots',return_value=plan), \
                     patch.object(ct,'write_week') as writer:
                    result=ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':td})})
                    self.assertFalse(result['documents'][-1]['valid'])
                    self.assertLess(result['content_delivery']['checked_posts'],2)
                    writer.assert_not_called()

    def test_finished_checkpoint_cannot_overrule_invalid_plan(self):
        slots=[{'week':1,'day':'Вт','topic':'Тема уникальная и понятная',
                'brief':'Разбор','goal':'Информирование','audience':'Аудитория',
                'format':'Reels','platform':'Instagram'},
               {'week':1,'day':'Вт','topic':'Дублированный календарный слот',
                'brief':'Разбор','goal':'Информирование','audience':'Аудитория',
                'format':'Reels','platform':'Instagram'}]
        _,blocked,verified=govern_plan(lambda *_:'{"issues":[]}','m','task',{},'',slots,
                                      {'1/Вт','1/Пт'},expected_ids=['1/Вт','1/Пт'])
        self.assertFalse(verified)
        self.assertTrue(blocked)

    def test_word_numbers_preserve_scope_or_fail_closed(self):
        from professional_content import requested_weeks
        from content_requirements import publication_scope
        for word, count in (('одиннадцать',11),('четырнадцать',14),('шестнадцать',16),
                            ('девятнадцать',19),('двадцать четыре',24)):
            with self.subTest(word=word):
                self.assertEqual(requested_weeks('на '+word+' недели'),count)
        self.assertEqual(publication_scope('две недели, по две публикации в неделю, всего четыре публикации').total,4)
        with self.assertRaisesRegex(ValueError,'от 1 до 7'):
            publication_scope('две недели, по восемь публикаций в неделю')
        with self.assertRaisesRegex(ValueError,'от 1 до 26'):
            requested_weeks('на двадцать семь недель')

class CurrentDocumentProvenanceTests(unittest.TestCase):
    def test_newer_user_document_blocks_fallback_to_old_receipt(self):
        import sqlite3,hashlib,json
        from task_artifacts import verified_document
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE manager_documents(id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, content TEXT, updated_at TEXT)')
            db.execute('CREATE TABLE ai_execution_journal(id INTEGER PRIMARY KEY, project_id TEXT, request TEXT, artifact_kind TEXT, artifact_sha256 TEXT, status TEXT, details TEXT)')
            old='Стратегия для предыдущего состояния проекта'
            db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?)',('old','p','strategy',old,'2026-01-01'))
            db.execute('INSERT INTO ai_execution_journal(project_id,request,artifact_kind,artifact_sha256,status,details) VALUES(?,?,?,?,?,?)',
                       ('p','request','strategy',hashlib.sha256(old.encode()).hexdigest(),'saved',json.dumps({'artifact_id':'old'})))
            self.assertEqual(verified_document(db,'p','request','strategy'),old)
            db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?)',('new','p','strategy','Новый документ после ручной правки','2026-01-02'))
            self.assertIsNone(verified_document(db,'p','request','strategy'))
            self.assertIsNone(verified_document(db,'another','request','strategy'))
        finally:db.close()
