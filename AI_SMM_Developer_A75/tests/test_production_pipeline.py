from substance_fixture import assessed_reader
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from document_recovery import DocumentDraft, patch_document
from strategy_recovery import produce_checked_strategy
from production_pipeline import run_document_task
from independent_quality_gate import final_quality_gate
from independent_acceptance_editor import ValidatedEditorialVerdict
from strategy_integrity import strategy_integrity_issues, separate_embedded_publication_section
import test_windows_acceptance_v4 as fixtures


class ProductionContractTests(unittest.TestCase):
    def test_strategy_artifact_separates_nested_publication_copies_before_validation(self):
        body=('## Стратегия\nКаналы и контентные направления.\n'
              '### Публикация 1\n- **Текст:** Первый полный текст.\n'
              '### Публикация 2\n- **Текст:** Второй полный текст.\n'
              '**Общие правила измерения:** Используй данные площадки.')
        separated,edits=separate_embedded_publication_section(body)
        issues=strategy_integrity_issues(separated,'Создай стратегию и календарь',per_week=2)
        self.assertEqual(len(edits),1)
        self.assertEqual(issues,[])
        self.assertNotIn('Первый полный текст',separated)
        self.assertIn('Общие правила измерения',separated)

    @patch('independent_acceptance_editor.call_substance_reader',new=assessed_reader)
    def test_random_independent_backup_is_used_only_after_unavailable_final_review(self):
        from production_pipeline import run_document_task
        reviewer_calls=[];events=[];captured={}
        def fake_editor(req,strategy,calendar,**kwargs):
            reviewer_calls.append(kwargs['model'])
            if kwargs['model']=='primary:7b':
                return {'pass':False,'issues':[{'type':'review_unavailable','document':'review'}],
                        'review_state':'unavailable'}
            return {'pass':True,'issues':[],'review_state':'complete'}
        def fake_compound(executor,request,context,progress=None,**kwargs):
            captured.update(context)
            return {'documents':[]}
        def fake_gate(result,request,review):
            verdict=review(request,'strategy','calendar')
            return {'verdict':verdict}
        with patch('generic_text_review.select_independent_model',side_effect=['primary:7b','backup:8b','reader:4b']), \
             patch('production_pipeline.run_compound_with_retry',side_effect=fake_compound), \
             patch('production_pipeline.final_quality_gate',side_effect=fake_gate), \
             patch('independent_acceptance_editor.call_editor',side_effect=fake_editor):
            result=run_document_task('request',{},progress=events.append,model='author:8b')
        self.assertEqual(reviewer_calls,['primary:7b','backup:8b'])
        self.assertEqual(captured['plan_reviewer_models'],['primary:7b','backup:8b'])
        self.assertTrue(any(event.get('stage')=='independent_reviewer_failover'
                            and event.get('stage_name')=='final' for event in events))
        self.assertTrue(result['verdict']['pass'])
        self.assertEqual(result['verdict']['independent_reader']['requested_model'],'reader:4b')

    def test_backup_explicit_rejection_without_findings_cannot_become_approval(self):
        # A valid independent backup may report pass=False with no detailed
        # findings. The merger must preserve that explicit rejection.
        calls = []
        def editor(req, strategy, calendar, **kwargs):
            calls.append(kwargs['model'])
            if kwargs['model'] == 'primary:7b':
                return {'pass': False, 'issues': [{'type': 'review_unavailable',
                        'document': 'review'}], 'review_state': 'unavailable'}
            return {'pass': False, 'issues': [], 'review_state': 'complete'}
        def gate(result, request, review):
            return {'verdict': review(request, 'strategy', 'calendar')}
        with patch('generic_text_review.select_independent_model',
                   side_effect=['primary:7b', 'backup:8b']), \
             patch('production_pipeline.run_compound_with_retry',
                   return_value={'documents': []}), \
             patch('production_pipeline.final_quality_gate', side_effect=gate), \
             patch('independent_acceptance_editor.call_editor', side_effect=editor):
            result = run_document_task('request', {}, model='author:4b')
        self.assertEqual(calls, ['primary:7b', 'backup:8b'])
        self.assertIs(result['verdict']['pass'], False)

    def test_malformed_native_editor_verdict_fails_over_without_auto_acceptance(self):
        from production_pipeline import run_document_task
        calls=[]
        def malformed_editor(req,strategy,calendar,**kwargs):
            calls.append(kwargs['model'])
            raise ValueError('Malformed assessments / issues contract')
        def fake_gate(result,request,review):
            verdict=review(request,'strategy','calendar')
            return {'verdict':verdict}
        with patch('generic_text_review.select_independent_model',side_effect=['primary:7b','backup:8b']), \
             patch('production_pipeline.run_compound_with_retry',return_value={'documents':[]}), \
             patch('production_pipeline.final_quality_gate',side_effect=fake_gate), \
             patch('independent_acceptance_editor.call_editor',side_effect=malformed_editor):
            result=run_document_task('request',{},model='author:4b')
        self.assertEqual(calls,['primary:7b','backup:8b'])
        self.assertFalse(result['verdict']['pass'])
        self.assertTrue(any(x.get('type')=='review_unavailable' for x in result['verdict']['issues']))

    def test_high_level_content_pillars_remain_valid_strategy_content(self):
        body=('## Контентные направления\nОбразовательные публикации, ответы на частые вопросы, '
              'редакторские заметки и короткие визуальные объяснения.')
        issues=strategy_integrity_issues(body,'Подготовь стратегию',per_week=2)
        self.assertEqual(issues,[])

    def test_both_entrypoints_use_same_gate(self):
        for name in ('app.py','real_workflow_acceptance.py'):
            sibling=Path(__file__).with_name(name)
            body=(sibling if sibling.is_file() else Path(__file__).parent.parent/name).read_text(encoding='utf-8')
            self.assertIn('run_text_task(',body)
            self.assertNotIn('run_compound_with_retry(',body)

    def test_final_failure_cannot_be_backend_success(self):
        original=fixtures.FinalEditorialGateTests.prepared()
        def execute(request,context,progress=None):
            self.assertTrue(context['strict_document_recovery'])
            self.assertEqual(context['strategy_reviewer']('strategy'),{'pass':True,'issues':[]})
            return original
        issue={'document':'strategy','type':'invented_fact',
               'quote':original['documents'][0]['content'],'reason':'Неподтверждённое утверждение о компании'}
        out=run_document_task('Запрос',{},execute=execute,
            strategy_reviewer=lambda *a:{'pass':True,'issues':[]},
            final_reviewer=lambda *a:{'pass':False,'issues':[issue]})
        self.assertFalse(out['documents'][0]['valid'])
        self.assertFalse(out['documents'][1]['valid'])
        self.assertTrue(original['documents'][0]['valid'])

    def test_randomized_acceptance_records_first_result_without_expensive_repair_replay(self):
        events=[]
        prepared=fixtures.FinalEditorialGateTests.prepared()
        issue={'document':'calendar','type':'quality',
               'quote':'Календарь для проекта. Место встречи не определено.',
               'reason':'Проверяемое содержательное замечание'}
        def blocked_gate(result,request,review):
            return {**result,'content_delivery':{
                **result['content_delivery'],
                'independent_editor':{'status':'BLOCKED','review_state':'rejected',
                                      'issues':[issue],'repair_slots':['1/Пн']}}}
        with patch('production_pipeline.run_compound_with_retry',return_value=prepared), \
             patch('production_pipeline.final_quality_gate',side_effect=blocked_gate):
            run_document_task('request',{'randomized_acceptance_run':True},
                progress=events.append,execute=lambda *a,**k:self.fail('sample replayed'),
                strategy_reviewer=lambda *a:{'pass':True,'issues':[]},
                final_reviewer=lambda *a:{'pass':False,'issues':[issue]})
        stages=[event.get('stage') for event in events]
        self.assertIn('autonomous_retry_skipped_acceptance_sample',stages)
        self.assertNotIn('autonomous_retry',stages)

    def test_editor_outage_blocks_both_documents(self):
        def offline(*a):raise ConnectionError('offline')
        result=final_quality_gate(fixtures.FinalEditorialGateTests.prepared(),'Запрос',offline)
        self.assertTrue(all(not x['valid'] for x in result['documents']))
        report=result['content_delivery']['independent_editor']
        self.assertEqual(report['review_state'],'unavailable')
        self.assertIn('повторного вызова',report['reason'])

    def test_host_validated_review_is_not_reparsed_by_final_gate(self):
        prepared=fixtures.FinalEditorialGateTests.prepared()
        canonical=ValidatedEditorialVerdict({'pass':False,'issues':[{
            'document':'calendar','type':'quality','quote':'точный сегмент календаря',
            'claim':'точный сегмент календаря','evidence_id':'calendar:0007',
            'reason':'Проверяемое замечание с уже проверенной ссылкой.'}],
            'disproved_absence_claims':[],'review_state':'complete'})
        out=final_quality_gate(prepared,'Запрос',lambda *a:canonical)
        review=out['content_delivery']['independent_editor']
        self.assertEqual(review['review_state'],'rejected')
        self.assertEqual(review['issues'][0]['evidence_id'],'calendar:0007')
        self.assertIn('Проверяемое замечание',review['reason'])

    def test_partial_review_outage_repairs_only_a_separately_verified_calendar_issue(self):
        prepared=fixtures.FinalEditorialGateTests.prepared()
        calendar=next(x for x in prepared['documents'] if x['kind']=='calendar')
        calendar['content'] += ('\n| Неделя/день | Площадка и формат | Конкретная тема | Цель | Сценарий/визуал | '
            'Текст/ключевые реплики | CTA | KPI | Метод измерения | Целевая аудитория | Необходимые материалы |\n'
            '|---|---|---|---|---|---|---|---|---|---|---|\n'
            '| 1/Вт | Instagram, пост | Topic | Goal | Visual | Unsupported process claim. | '
            'Ask us | KPI | Method | Audience | Materials |')
        issue={'document':'calendar','type':'invented_fact',
            'quote':'Unsupported process claim.','claim':'Unsupported process claim.',
            'evidence_segment':'Unsupported process claim.','evidence_id':'calendar:0008',
            'row_context':{'slot':'1/Вт'},'adjudication':'unsupported',
            'reason':'The claim is not supported by project sources.'}
        unavailable={'document':'calendar','type':'review_unavailable','quote':'',
            'adjudication':'unavailable','reason':'The source registry did not finish.'}
        verdict=ValidatedEditorialVerdict({'pass':False,'issues':[issue,unavailable],
            'review_state':'unavailable','disproved_absence_claims':[]})
        calls=[]
        def reviewer(*args):
            calls.append(args)
            return verdict
        blocked=final_quality_gate(prepared,'Request',reviewer)
        report=blocked['content_delivery']['independent_editor']
        self.assertEqual(report['status'],'BLOCKED')
        self.assertEqual(report['review_state'],'unavailable')
        self.assertEqual(report['repair_slots'],['1/Вт'])
        self.assertEqual(report['issues'],[issue])
        self.assertTrue(report['validation_diagnostics'])

    def test_partial_review_outage_triggers_bounded_repair_of_only_unapproved_calendar(self):
        prepared=fixtures.FinalEditorialGateTests.prepared()
        original_strategy=prepared['documents'][0]['content']
        original_calendar=next(x for x in prepared['documents'] if x['kind']=='calendar')['content']
        calendar=next(x for x in prepared['documents'] if x['kind']=='calendar')
        calendar['content'] += ('\n| Неделя/день | Площадка и формат | Конкретная тема | Цель | Сценарий/визуал | '
            'Текст/ключевые реплики | CTA | KPI | Метод измерения | Целевая аудитория | Необходимые материалы |\n'
            '|---|---|---|---|---|---|---|---|---|---|---|\n'
            '| 1/Вт | Instagram, пост | Topic | Goal | Visual | Unsupported process claim. | '
            'Ask us | KPI | Method | Audience | Materials |')
        issue={'document':'calendar','type':'invented_fact',
            'quote':'Unsupported process claim.','claim':'Unsupported process claim.',
            'evidence_segment':'Unsupported process claim.','evidence_id':'calendar:0008',
            'row_context':{'slot':'1/Вт'},'adjudication':'unsupported',
            'reason':'The claim is not supported by project sources.'}
        unavailable={'document':'calendar','type':'review_unavailable','quote':'',
            'adjudication':'unavailable','reason':'The source registry did not finish.'}
        calls=[];contexts=[];events=[]
        def reviewer(*args):
            calls.append(True)
            if len(calls)==1:
                return ValidatedEditorialVerdict({'pass':False,'issues':[issue,unavailable],
                    'review_state':'unavailable','disproved_absence_claims':[]})
            return ValidatedEditorialVerdict({'pass':True,'issues':[],
                'review_state':'complete','disproved_absence_claims':[]})
        def execute(request,context,progress=None):
            contexts.append(dict(context))
            result=fixtures.FinalEditorialGateTests.prepared() if context.get('task_mode')=='final_review_repair' else prepared
            if context.get('task_mode')=='final_review_repair':
                self.assertEqual(context['final_review_repair_slots'],['1/Вт'])
                self.assertEqual(context['resume_documents'][0]['content'],original_strategy)
            return result
        out=run_document_task('Request',{},execute=execute,
            strategy_reviewer=lambda *a:{'pass':True,'issues':[]},
            final_reviewer=reviewer,progress=events.append)
        self.assertEqual(len(contexts),2)
        self.assertEqual(contexts[1]['task_mode'],'final_review_repair')
        self.assertEqual(out['content_delivery']['independent_editor']['status'],'PASS')
        self.assertTrue(any(e.get('stage')=='autonomous_retry' for e in events))

    def test_transient_review_failure_retries_same_content_without_rewriting(self):
        prepared=fixtures.FinalEditorialGateTests.prepared()
        before=[(x['kind'],x['content']) for x in prepared['documents']]
        calls=[]
        def reviewer(*args):
            calls.append(args)
            if len(calls)==1:
                raise ValueError('незавершённый реестр доказательств')
            return {'pass':True,'issues':[]}
        result=final_quality_gate(prepared,'Запрос',reviewer)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0],calls[1])
        self.assertEqual([(x['kind'],x['content']) for x in prepared['documents']],before)
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'PASS')

    def test_valid_rejection_is_not_retried_as_a_transient_review_failure(self):
        prepared=fixtures.FinalEditorialGateTests.prepared()
        calls=[]
        issue={'document':'calendar','type':'quality','quote':'календарь',
               'reason':'Обнаружена проверяемая проблема качества материала.'}
        def reviewer(*args):
            calls.append(args)
            return {'pass':False,'issues':[issue]}
        result=final_quality_gate(prepared,'Запрос',reviewer)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['content_delivery']['independent_editor']['review_state'],'rejected')

    def test_backup_merge_preserves_host_validated_verdict_marker(self):
        from unittest.mock import patch
        from independent_acceptance_editor import ValidatedEditorialVerdict
        original=fixtures.FinalEditorialGateTests.prepared()
        calls=[]
        def reviewer(*args,**kwargs):
            calls.append(True)
            if len(calls)==1:
                return {'pass':False,'issues':[{'document':'review','type':'review_unavailable',
                    'quote':'','reason':'Primary reviewer unavailable.'}],
                    'review_state':'unavailable'}
            return ValidatedEditorialVerdict({'pass':True,'issues':[],
                'disproved_absence_claims':[],'review_state':'complete'})
        def execute(*args,**kwargs): return original
        with patch('independent_quality_gate.parse_verdict',side_effect=AssertionError(
                'host-validated failover result must not be parsed as a legacy dictionary')):
            result=run_document_task('Create materials',{},execute=execute,
                strategy_reviewer=lambda *a:{'pass':True,'issues':[]},
                final_reviewer=reviewer)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'PASS')

    def test_final_calendar_finding_retries_only_the_cited_unapproved_slot(self):
        original=fixtures.FinalEditorialGateTests.prepared()
        row=('| 1/Вт | Instagram, Карусель | Проверяемая тема | Цель | Визуальный сценарий с кадрами | '
             'Ремонт позволяет избежать любых поломок после обслуживания. | Напишите вопрос | '
             'Ответы; измерение по сообщениям | Instagram Insights | Владельцы велосипедов | Фото деталей |')
        original['documents'][1]['content']=('| Неделя/день | Площадка и формат | Конкретная тема | Цель | Сценарий/визуал | '
            'Текст/ключевые реплики | CTA | KPI | Метод измерения | Целевая аудитория | Необходимые материалы |\n'
            '|---|---|---|---|---|---|---|---|---|---|---|\n'+row)
        contexts=[]
        def execute(request,context,progress=None):
            contexts.append(dict(context))
            return original
        issue={'document':'calendar','type':'invented_fact',
               'quote':'Ремонт позволяет избежать любых поломок после обслуживания.',
               'evidence_segment':'Ремонт позволяет избежать любых поломок после обслуживания.',
               'evidence_id':'calendar:0008','row_context':{'slot':'1/Вт'},
               'reason':'Этот факт в публикации не подтверждён запросом пользователя.'}
        calls=[]
        def reviewer(*args):
            calls.append(True)
            return (ValidatedEditorialVerdict({'pass':False,'issues':[issue],
                                               'review_state':'complete'})
                    if len(calls)==1 else {'pass':True,'issues':[]})
        events=[]
        result=run_document_task('Создай материалы',{},execute=execute,
            strategy_reviewer=lambda *a:{'pass':True,'issues':[]},
            final_reviewer=reviewer,progress=events.append)
        self.assertEqual(len(contexts),2)
        self.assertEqual(contexts[1]['task_mode'],'final_review_repair')
        self.assertEqual(contexts[1]['final_review_repair_slots'],['1/Вт'])
        self.assertEqual(contexts[1]['resume_documents'][0]['kind'],'strategy')
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'PASS')
        self.assertTrue(any(e.get('stage')=='autonomous_retry' for e in events))

    def test_final_review_stops_when_same_artifact_repeats_same_rejection(self):
        original=fixtures.FinalEditorialGateTests.prepared()
        row='| 1/Вт | Instagram, Карусель | Проверяемая тема | Цель | Визуальный сценарий с кадрами | Полный текст публикации достаточной длины для проверки | Напишите вопрос | Ответы; измерение по сообщениям |'
        original['documents'][1]['content']=('| Неделя/день | Площадка и формат | Тема | Цель | Сценарий/визуал | Текст/ключевые реплики | CTA | KPI |\n'
            '|---|---|---|---|---|---|---|---|\n'+row)
        strategy=original['documents'][0]['content']
        contexts=[]
        def execute(request,context,progress=None):
            contexts.append(dict(context))
            return original
        issue={'document':'calendar','type':'invented_fact','quote':row,
               'reason':'Нужно исправить неподтверждённое утверждение в черновике.'}
        calls=[]
        def reviewer(*args):
            calls.append(True)
            return {'pass':False,'issues':[issue]}
        events=[]
        result=run_document_task('Создай материалы',{},execute=execute,
            strategy_reviewer=lambda *a:{'pass':True,'issues':[]},
            final_reviewer=reviewer,progress=events.append)
        self.assertEqual(len(contexts),2)
        self.assertEqual(contexts[1]['task_mode'],'final_review_repair')
        self.assertEqual(contexts[1]['final_review_repair_slots'],['1/Вт'])
        self.assertEqual(result['documents'][0]['content'],strategy)
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'BLOCKED')
        self.assertIn('autonomous_retry_stopped_no_progress', [e.get('stage') for e in events])

    def test_saved_strategy_is_never_sent_to_final_repair(self):
        original=fixtures.FinalEditorialGateTests.prepared()
        saved={'kind':'strategy','content':original['documents'][0]['content'],'already_saved':True}
        calls=[]
        issue={'document':'strategy','type':'invented_fact',
               'quote':original['documents'][0]['content'],'reason':'Есть неподтверждённое утверждение.'}
        def execute(*args,**kwargs):
            calls.append(True)
            return original
        result=run_document_task('Запрос',{'resume_documents':[saved]},execute=execute,
            strategy_reviewer=lambda *a:{'pass':True,'issues':[]},
            final_reviewer=lambda *a:{'pass':False,'issues':[issue]})
        self.assertEqual(len(calls),1)
        self.assertEqual(result['documents'][0]['content'],saved['content'])
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'BLOCKED')

    def test_strategy_only_review_uses_no_fake_calendar(self):
        from independent_acceptance_editor import call_strategy_editor
        with patch('independent_acceptance_editor._call_editor_once',return_value={'pass':True,'issues':[]}) as call:
            self.assertTrue(call_strategy_editor('Задание','Стратегия')['pass'])
            self.assertEqual(call.call_args.args,('Задание','Стратегия',''))
            self.assertTrue(call.call_args.kwargs['strategy_only'])


class DocumentRecoveryTests(unittest.TestCase):
    def test_unavailable_independent_review_does_not_trigger_document_repair(self):
        from independent_acceptance_editor import IndependentReviewUnavailable
        generated=[];events=[]
        def generate(*args):
            generated.append(True)
            return 'Draft strategy preserved for later review.'
        def unavailable(*args):
            raise IndependentReviewUnavailable('review output did not satisfy the evidence contract')
        def forbidden_repair(*args):
            self.fail('Validator outage is not a content defect')
        result=produce_checked_strategy('Create a strategy',2,generate,lambda *a:(True,''),
            attempts=3,repair=forbidden_repair,review=unavailable,
            report=lambda stage,**data:events.append(stage))
        self.assertFalse(result[1])
        self.assertEqual(len(generated),1)
        self.assertIn('недоступна',result[2])
        self.assertIn('strategy_review_unavailable',events)

    def test_task_executor_does_not_repeat_when_strategy_review_is_unavailable(self):
        from compound_retry import run_compound_with_retry
        calls=[]
        def execute(*args,**kwargs):
            calls.append(True)
            return {'action':'compound_documents','documents':[],
                    'failure_code':'independent_review_unavailable'}
        result=run_compound_with_retry(execute,'Request',{},max_attempts=3)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['failure_code'],'independent_review_unavailable')

    def test_rejected_document_survives_restart_without_regeneration(self):
        with tempfile.TemporaryDirectory() as root:
            store=DocumentDraft(root,'task-signature','strategy')
            bad='База: 1000 просмотров'
            result=produce_checked_strategy('Создай стратегию',3,lambda *a:bad,
                lambda *a:(True,''),attempts=1,checkpoint=store.save,
                review=lambda s:{'pass':False,'issues':[{'reason':'База не подтверждена','quote':s}]})
            self.assertFalse(result[1])
            restored=DocumentDraft(root,'task-signature','strategy').load()
            def no_generate(*a):self.fail('Restart must reuse rejected draft')
            def repair(body,reason,attempt):
                self.assertEqual(body,bad);self.assertIn('База не подтверждена',reason)
                return 'База неизвестна; требуется измерение.'
            result=produce_checked_strategy('Создай стратегию',3,no_generate,lambda *a:(True,''),
                draft=restored,repair=repair,checkpoint=store.save,review=lambda s:{'pass':True,'issues':[]})
            self.assertTrue(result[1]);self.assertEqual(store.load()['content'],result[0])

    def test_corruption_cannot_reset_progress(self):
        with tempfile.TemporaryDirectory() as root:
            store=DocumentDraft(root,'task','strategy');store.save('body',False,'reason')
            data=json.loads(store.path.read_text(encoding='utf-8'));data['content']='tampered'
            store.path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):store.load()

    def test_patch_preserves_unmodified_bytes_and_line_endings(self):
        body='# Стратегия\r\nОшибка\r\nПоследняя строка'
        fixed=patch_document(lambda *a:json.dumps({'changes':[{'line_id':'L00002','text':'Исправление'}]}),
                             'model','request',body,'reason')
        self.assertEqual(fixed,'# Стратегия\r\nИсправление\r\nПоследняя строка')

    def test_invalid_patch_keeps_checkpoint(self):
        for change in ([{'line_id':'L99999','text':'x'}],
                       [{'line_id':'L00001','text':'heading'}],
                       [{'line_id':'L00002','text':'x'},{'line_id':'L00002','text':'y'}],
                       [{'line_id':'L00002','text':'old'}]):
            with self.subTest(change=change),self.assertRaises(ValueError):
                patch_document(lambda *a:json.dumps({'changes':change}),'m','r','# Heading\nold','why')

    def test_patch_allows_bounded_multiline_replacement_inside_one_prose_line(self):
        body='# Heading\nUnsupported claim. Adjacent useful text.\nTail.'
        fixed=patch_document(lambda *a:json.dumps({'changes':[
            {'line_id':'L00002','text':'Neutral wording.\nQuestion for confirmation.'}]}),
            'm','r',body,'Unsupported claim')
        self.assertEqual(fixed,'# Heading\nNeutral wording.\nQuestion for confirmation.\nTail.')

    def test_patch_preserves_table_shape_and_list_marker(self):
        for body,text in [('| Fact | Detail |','| Repaired | Detail |\n| Extra | Row |'),
                          ('- Unsupported detail','* changed marker')]:
            with self.subTest(body=body),self.assertRaises(ValueError):
                patch_document(lambda *a,t=text:json.dumps({'changes':[{'line_id':'L00001','text':t}]}),
                               'm','r',body,'reason')
        fixed=patch_document(lambda *a:json.dumps({'changes':[{'line_id':'L00001',
            'text':'Unsupported detail removed'}]}),'m','r','- Unsupported detail','reason')
        self.assertEqual(fixed,'- Unsupported detail removed')

    def test_saved_strategy_is_immutable_under_semantic_rejection(self):
        def forbidden(*a):self.fail('Accepted content must not be rewritten')
        result=produce_checked_strategy('Задание',3,forbidden,lambda *a:(True,''),
            existing='Сохранённая стратегия',repair=forbidden,checkpoint=forbidden,
            review=lambda s:{'pass':False,'issues':[{'reason':'Требуется согласование изменений','quote':s}]})
        self.assertFalse(result[1]);self.assertEqual(result[0],'Сохранённая стратегия')
