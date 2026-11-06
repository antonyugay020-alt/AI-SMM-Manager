"""Cross-domain state, interpretation, audit and failure-mode regression."""
import json
import tempfile
import unittest
from pathlib import Path

from professional_content import requested_weeks
from content_requirements import publication_scope, delivery_audit, PublicationScope
from task_orchestration import TaskLedger
from task_routing import resolve_request
from plan_governance import govern_plan, parse_review


def complete_post(ident):
    return {**{k:'проверенное значение' for k in ('caption','visual','cta','kpi','audience','measurement','materials')},'id':ident,'topic':'различная тема'}


class UniversalArchitectureRegression(unittest.TestCase):
    def test_worded_and_excessive_periods_not_silently_substituted(self):
        self.assertEqual(requested_weeks('Контент-план на две недели'), 2)
        self.assertEqual(requested_weeks('План на три недели'), 3)
        self.assertEqual(requested_weeks('План на двадцать недель'), 20)
        with self.assertRaisesRegex(ValueError, '26'):
            requested_weeks('План на 30 недель')
        with self.assertRaisesRegex(ValueError, '26'):
            requested_weeks('План на 27 недель')

    def test_total_and_frequency_derive_period_only_when_unspecified(self):
        result=publication_scope('Нужно ровно 10 публикаций, по 2 публикации в неделю')
        self.assertEqual((result.weeks,result.per_week,result.total),(5,2,10))
        with self.assertRaisesRegex(ValueError, 'Противоречивые'):
            publication_scope('Нужно на три недели, по 2 публикации в неделю, всего 10 публикаций')

    def test_ledger_idempotency_and_additional_required_output(self):
        with tempfile.TemporaryDirectory() as td:
            ledger=TaskLedger.open(Path(td),'universal')
            ledger.register('document',['strategy','calendar'])
            ledger.reconcile([],strategy=True,calendar=True)
            self.assertTrue(ledger.completed)
            revision=ledger.outputs['strategy']['revisions']
            ledger.set_status('strategy','accepted')
            self.assertTrue(ledger.completed)
            self.assertEqual(ledger.outputs['strategy']['revisions'],revision)
            ledger.register('document',['recommendations'])
            self.assertFalse(ledger.completed)
            reopened=TaskLedger.open(Path(td),'universal')
            self.assertFalse(reopened.completed)
            self.assertIn('recommendations',reopened.outputs)

    def test_ledger_bad_json_shape_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'task_orchestration_deadbeef.json'
            path.write_text('[]',encoding='utf-8')
            self.assertEqual(TaskLedger.open(td,'deadbeef').outputs,{})
            path.write_text(json.dumps({'signature':'deadbeef','outputs':{'a':{'kind':'x','status':'accepted','revisions':[]}},'completed':True}),encoding='utf-8')
            self.assertFalse(TaskLedger.open(td,'deadbeef').completed)

    def test_report_rejects_payload_count_only(self):
        docs=[{'kind':'strategy','valid':True,'content':'стратегия'},
              {'kind':'calendar','valid':True,'content':'календарь'}]
        scope=PublicationScope(2,2,4)
        incomplete={ident:complete_post(ident) for ident in ('1/Вт','1/Пт','2/Вт','2/Пт')}
        incomplete['2/Пт']['caption']='  '
        self.assertTrue(delivery_audit(docs,scope,incomplete))
        extra={ident:complete_post(ident) for ident in ('1/Вт','1/Пт','2/Вт','3/Пт')}
        self.assertTrue(delivery_audit(docs,scope,extra))
        valid={ident:complete_post(ident) for ident in ('1/Вт','1/Пт','2/Вт','2/Пт')}
        self.assertEqual(delivery_audit(docs,scope,valid),[])

    def test_bad_plan_auditor_output_never_means_approval(self):
        self.assertIsNone(parse_review('{"issues":[{"id":"9/Пн","reason":"ошибка"}]}',{'1/Пн'}))
        self.assertIsNone(parse_review('{"issues":["некорректная запись"]}',{'1/Пн'}))
        slot={'week':1,'day':'Пн','topic':'Первичная проверка','format':'Reels'}
        _,blocked,verified=govern_plan(lambda *_:'{"issues":[{"id":"9/Пн","reason":"wrong"}]}',
                                       'model','Задание',{},'стратегия',[slot],set(),expected_ids=['1/Пн'])
        self.assertFalse(verified)

    def test_resume_free_form_not_reinterpreted_as_new(self):
        previous='Создай стратегию и план для книжного магазина'
        for command in ('Продолжи незавершённое задание. Сохрани все 29 готовых публикаций без изменений.',
                        'Доработай незавершённую задачу', 'Заверши проект'):
            with self.subTest(command=command):
                self.assertEqual(resolve_request(command,previous),(previous,'resume'))
        self.assertEqual(resolve_request('Создай новый план для ресторана',previous)[1],'new')
        self.assertEqual(resolve_request('Измени готовый проект: переработай финальную стратегию',previous)[1],'new')


if __name__=='__main__': unittest.main()

class DamagedPersistenceRegression(unittest.TestCase):
    def test_bad_project_facts_type_cannot_poison_context(self):
        import sqlite3
        from task_context import load_facts, update_facts
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE ai_task_facts(project_id TEXT PRIMARY KEY, facts_json TEXT NOT NULL)')
            for raw in ('[]','null','123','"unexpected"','{"business":"книжный магазин"}'):
                db.execute('INSERT OR REPLACE INTO ai_task_facts VALUES(?,?)',('project-X',raw))
                values=load_facts(db,'project-X')
                self.assertIsInstance(values,dict)
                update_facts(db,'project-X','Новый запрос')
        finally:db.close()

    def test_corrupt_checkpoint_json_shape_fails_gracefully(self):
        import hashlib
        from unittest.mock import patch
        import compound_tasks as ct
        original='Разработай стратегию и контент-план на 2 недели, по 2 публикации в неделю, всего 4 публикации'
        for name in ('publication_progress','publication_repair','topic_plan'):
            for body in ('[]','null','123','"invalid"'):
                with self.subTest(name=name,body=body), tempfile.TemporaryDirectory() as td:
                    sig=hashlib.sha256((td+'\n'+original+'\n2\n2').encode()).hexdigest()
                    drafts=Path(td)/'drafts'; drafts.mkdir()
                    (drafts/f'{name}_{sig[:16]}.json').write_text(body,encoding='utf-8')
                    with patch.object(ct,'analyze',side_effect=RuntimeError('synthetic offline stop')):
                        output=ct.execute_compound(original,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':td})})
                    self.assertIn('error',output)
                    self.assertIn('synthetic offline stop',output['error'])

class DiagnosticBoundaryRegression(unittest.TestCase):
    def test_corrupt_settings_and_progress_shapes_are_reported(self):
        from diagnostic_monitor import discover_projects, inspect_project
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for data in ('[]', 'null','"text"','123'):
                (root/'storage_settings.json').write_text(data,encoding='utf-8')
                locations,warnings,roots=discover_projects(root)
                self.assertIsInstance(locations,list)
                self.assertIsInstance(warnings,list)
            project=root/'projects'/'p';drafts=project/'drafts';drafts.mkdir(parents=True)
            for data in ('[]','null','"text"','123',json.dumps({'posts':{},'total':'invalid','completed':0})):
                (drafts/'publication_progress_1.json').write_text(data,encoding='utf-8')
                report=inspect_project(project)
                self.assertTrue(report['issues'])

    def test_invalid_nested_diagnostic_events_cannot_abort_monitor(self):
        from diagnostic_monitor import inspect_project
        with tempfile.TemporaryDirectory() as td:
            project=Path(td);drafts=project/'drafts';drafts.mkdir()
            events=[{'stage':'batch_attempt','errors':None},
                    {'stage':'single_quality_rejected','issues':2},
                    {'stage':'batch_attempt','errors':{'unexpected':'value'}}]
            (drafts/'publication_diagnostics_1.jsonl').write_text(
                '\n'.join(json.dumps(x) for x in events),encoding='utf-8')
            report=inspect_project(project)
            self.assertIsInstance(report,dict)
            self.assertTrue(report['runs'])

class PlanOrchestrationFailureRegression(unittest.TestCase):
    def test_unhandled_plan_preflight_error_blocks_generation_without_affecting_accepted(self):
        from unittest.mock import patch
        import compound_tasks as ct
        from plan_review_fixture import with_plan_review
        request='Создай SMM-стратегию и контент-план на 1 неделю, по 2 публикации в неделю, всего 2 публикации'
        slots=[dict(week=1,day=day,topic=f'Разная информационная тема {day}',format='Reels',
                    platform='Instagram',goal='Информирование',audience='Читатели',brief='Сравнение критериев')
               for day in ('Вт','Пт')]
        with tempfile.TemporaryDirectory() as td:
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review('# Стратегия\n'+'Цели, аудитория, каналы, KPI. '*55)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',return_value=slots), \
                 patch.object(ct,'govern_plan',side_effect=RuntimeError('synthetic auditor crash')), \
                 patch.object(ct,'write_week') as writer:
                result=ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':td})})
            self.assertFalse(result['documents'][-1]['valid'])
            self.assertEqual(result['content_delivery']['checked_posts'],0)
            writer.assert_not_called()
            self.assertEqual(set(result['content_delivery']['plan_preflight']['unresolved_slots']),{'1/Пт','1/Вт'})

class AcceptedArtifactImmutabilityRegression(unittest.TestCase):
    def test_policy_update_cannot_silently_replace_old_accepted_content(self):
        from unittest.mock import patch
        import compound_tasks as ct
        from plan_review_fixture import with_plan_review
        from test_stage69_scope import good_post
        from semantic_editor import REVIEW_VERSION
        import hashlib
        request='Создай SMM-стратегию и контент-план на 1 неделю, по 2 публикации в неделю, всего 2 публикации'
        slots=[{'week':1,'day':day,'topic':f'Различная информационная тема {day}',
                'format':'Reels','platform':'Instagram','goal':'Объяснение','audience':'Читатели','brief':'Тест'}
               for day in ('Вт','Пт')]
        previous=good_post(slots[0]);previous['_editorial_review']=REVIEW_VERSION
        previous['_editorial_policy']='previous_editorial_policy'
        with tempfile.TemporaryDirectory() as td:
            sig=hashlib.sha256((td+'\n'+request+'\n1\n2').encode()).hexdigest()
            drafts=Path(td)/'drafts'; drafts.mkdir()
            check=drafts/f'publication_progress_{sig[:16]}.json'
            check.write_text(json.dumps({'signature':sig,'posts':{'1/Вт':previous},'total':2,'completed':1},ensure_ascii=False))
            def reviewer(ask,model,req,facts,slot,post,*_):
                if slot['day']=='Вт': return None,['Новые требования редактора']
                return post,[]
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review('# Стратегия\n'+('Цели, задачи и KPI. '*70))), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',return_value=slots), \
                 patch.object(ct,'write_week',side_effect=lambda a,b,c,d,e,ss,*args,**kwargs:[good_post(s) for s in ss]) as writer, \
                 patch.object(ct,'audit_post',side_effect=reviewer), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                result=ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':td})})
            stored=json.loads(check.read_text(encoding="utf-8"))['posts']
            self.assertEqual(stored['1/Вт'],previous)
            self.assertFalse(result['documents'][-1]['valid'])  # report the policy mismatch, do not mutate
            for call in writer.call_args_list:
                self.assertTrue(all(s['day']!='Вт' for s in call.args[5]))

class DeliveryCompletenessRegression(unittest.TestCase):
    def test_four_posts_concentrated_in_one_week_do_not_pass_two_week_plan(self):
        docs=[{'kind':'strategy','valid':True,'content':'strategy'},
              {'kind':'calendar','valid':True,'content':'calendar'}]
        scope=PublicationScope(2,2,4)
        posts={name:complete_post(name) for name in ('1/Пн','1/Вт','1/Ср','2/Пт')}
        self.assertTrue(delivery_audit(docs,scope,posts))

    def test_invalid_delivery_data_and_duplicate_documents_do_not_claim_completion(self):
        from content_requirements import delivery_report
        scope=PublicationScope(1,2,2)
        docs=[{'kind':'strategy','valid':False,'content':'bad'},
              {'kind':'strategy','valid':True,'content':'good'},
              {'kind':'calendar','valid':True,'content':'calendar'}]
        self.assertTrue(delivery_audit(docs,scope,{'1/Вт':complete_post('1/Вт'),'1/Пт':complete_post('1/Пт')}))
        report=delivery_report('Контент-план',scope,docs,None)
        self.assertIn('unfulfilled',report)
        self.assertTrue(report['unfulfilled'])

class DeterministicFuzzRegression(unittest.TestCase):
    def test_malformed_delivery_documents_never_crash_or_pass(self):
        import random
        rng=random.Random(762026)
        primitive=[None,[],{},1,-1,True,'', 'strategy', ['strategy'],{'untrusted':'value'}]
        from content_requirements import delivery_report
        scope=PublicationScope(2,2,4)
        for n in range(250):
            docs=[{'kind':rng.choice(primitive), 'valid':rng.choice(primitive),
                   'content':rng.choice(primitive)} for _ in range(2)]
            posts={f'{n%4+1}/Пн':complete_post(f'{n%4+1}/Пн')}
            audit=delivery_audit(docs,scope,posts)
            self.assertTrue(audit)
            report=delivery_report('Контент-план',scope,docs,posts)
            self.assertTrue(report['unfulfilled'])

    def test_task_status_random_transitions_never_silently_reopen_accepted(self):
        import random
        rng=random.Random(206)
        with tempfile.TemporaryDirectory() as td:
            ledger=TaskLedger.open(td,'stress')
            ids=['strategy','calendar']+[f'{w}/Пн' for w in range(1,7)]
            ledger.register('artifact',ids)
            for _ in range(200):
                ident=rng.choice(ids)
                current=ledger.outputs[ident]['status']
                target=rng.choice(['planned','running','blocked','accepted'])
                if current=='accepted' and target!='accepted':
                    with self.assertRaises(ValueError):ledger.set_status(ident,target)
                else:
                    ledger.set_status(ident,target)
                revived=TaskLedger.open(td,'stress')
                self.assertEqual(revived.outputs,ledger.outputs)
                self.assertEqual(revived.completed,ledger.completed)

class MultiDomainPipelineRegression(unittest.TestCase):
    def test_five_unrelated_projects_use_same_lifecycle_without_leaking_checkpoints(self):
        import compound_tasks as ct
        from unittest.mock import patch
        from test_stage69_scope import good_post, mini_plan
        from plan_review_fixture import with_plan_review
        categories=('магазина книг','автомастерской','салона красоты',
                    'туристического агентства','студии звукозаписи')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review('# Стратегия\n'+('Цели, аудитория, бюджет, метрики. '*75))), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',side_effect=mini_plan), \
                 patch.object(ct,'write_week',side_effect=lambda ask,m,req,facts,strategy,ss,prev,feedback='':[good_post(s) for s in ss]) as writer, \
                 patch.object(ct,'audit_post',side_effect=lambda ask,model,req,known,slot,post,*_: (post,[])), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                for n,category in enumerate(categories):
                    req=f'Разработай SMM-стратегию и контент-план для {category} на 2 недели, по 2 публикации в неделю, всего 4 публикации'
                    project=root/f'project_{n}'
                    first=ct.execute_compound(req,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':str(project)})})
                    self.assertTrue(first['documents'][-1]['valid'],first['documents'][-1]['reason'])
                    before=writer.call_count
                    repeat=ct.execute_compound(req,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':str(project),'task_mode':'resume'})})
                    self.assertTrue(repeat['documents'][-1]['valid'],repeat['documents'][-1]['reason'])
                    self.assertEqual(writer.call_count,before)
                self.assertEqual(len(list(root.glob('project_*/drafts/publication_progress_*.json'))),len(categories))

class CheckpointQuarantineRegression(unittest.TestCase):
    def test_bad_saved_post_is_quarantined_and_valid_other_post_preserved(self):
        import hashlib
        import compound_tasks as ct
        from unittest.mock import patch
        from test_stage69_scope import good_post
        from plan_review_fixture import with_plan_review
        request='Разработай стратегию и контент-план на 1 неделю, по 2 публикации в неделю, всего 2 публикации'
        slots=[dict(week=1,day=day,topic=f'Критерий {day}',format='Reels',
                    platform='Instagram',goal='Польза',audience='Читатели',brief='Шаги') for day in ('Вт','Пт')]
        good=good_post(slots[0])
        with tempfile.TemporaryDirectory() as td:
            sig=hashlib.sha256((td+'\n'+request+'\n1\n2').encode()).hexdigest()
            drafts=Path(td)/'drafts';drafts.mkdir()
            progress=drafts/f'publication_progress_{sig[:16]}.json'
            original={'signature':sig,'posts':{'1/Вт':good,'1/Пт':None},'completed':2,'total':2}
            progress.write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
            with patch.object(ct,'analyze',side_effect=RuntimeError('offline stop')):
                result=ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':td})})
            self.assertIn('error',result)
            # Legacy checkpoints are immutable migration input. A failed
            # execution must not rewrite them simply to satisfy a test.
            restored=json.loads(progress.read_text(encoding='utf-8'))['posts']
            self.assertEqual(restored.get('1/Вт'),good)
            self.assertIn('1/Пт',restored)
            self.assertIsNone(restored['1/Пт'])
            quarantines=list(drafts.glob('publication_progress_*.quarantine.json'))
            self.assertTrue(quarantines)
            self.assertEqual(json.loads(quarantines[0].read_text(encoding='utf-8'))['posts'],restored)
