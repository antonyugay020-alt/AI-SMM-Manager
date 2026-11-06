"""Architecture-first regression: durable state + plan-wide preflight before writing."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks as ct
import plan_governance as pg
from task_orchestration import TaskLedger
from test_stage69_scope import good_post, mini_plan
from semantic_editor import REVIEW_VERSION, POLICY_VERSION


class DurableTaskStateTests(unittest.TestCase):
    def test_all_required_artifacts_and_resume_identity(self):
        with tempfile.TemporaryDirectory() as home:
            ledger=TaskLedger.open(Path(home),'task-A')
            ledger.register('document',['strategy','calendar'])
            ledger.register('publication',['1/Вт','1/Пт'])
            ledger.reconcile(['1/Вт'],strategy=True)
            self.assertFalse(ledger.completed)
            self.assertEqual(ledger.summary()['counts']['accepted'],2)
            restored=TaskLedger.open(Path(home),'task-A')
            self.assertEqual(restored.outputs,ledger.outputs)
            restored.set_status('1/Пт','blocked',reason='Неверный план')
            with self.assertRaises(ValueError):
                restored.set_status('1/Вт','running')
            restored.set_status('1/Пт','accepted')
            restored.set_status('calendar','accepted')
            restored.reconcile(['1/Вт','1/Пт'],strategy=True,calendar=True)
            self.assertTrue(restored.completed)
            self.assertTrue(TaskLedger.open(Path(home),'task-A').completed)
            other=TaskLedger.open(Path(home),'task-B')
            self.assertEqual(other.outputs,{})

    def test_final_review_repair_saves_draft_before_checkpoint_is_reopened(self):
        request=('Разработай SMM-стратегию и контент-план для студии керамики, '
                 '1 неделя, 2 публикации в неделю, всего 2 публикации')
        post={'id':'1/Вт','topic':'Фактура глины','caption':'Отклонённый черновик',
              'visual':'Съёмка глиняной фактуры','cta':'Задайте вопрос',
              'kpi':'Вопросы','audience':'Начинающие мастера',
              'measurement':'Подсчёт вопросов','materials':'Глина и инструменты'}
        with tempfile.TemporaryDirectory() as home:
            drafts=Path(home)/'drafts'; drafts.mkdir()
            import hashlib
            signature=hashlib.sha256((home+'\n'+request+'\n1\n2').encode()).hexdigest()
            progress_file=drafts/f'publication_progress_{signature}.json'
            original=json.dumps({'signature':signature,'posts':{'1/Вт':post},
                                 'completed':1,'total':2},ensure_ascii=False).encode()
            progress_file.write_bytes(original)
            events=[]
            finding={'document':'calendar','type':'invented_fact',
                     'quote':'В ателье каждому клиенту гарантируют комфортный результат.',
                     'evidence_id':'calendar:0011','field_role':'Текст/ключевые реплики',
                     'reason':'Обещание не подтверждено исходными материалами.'}
            class StopAfterRepair(Exception): pass
            with patch.object(ct,'extract_facts',side_effect=StopAfterRepair), \
                 self.assertRaises(StopAfterRepair):
                ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({
                    'project_workspace':home,
                    'task_mode':'final_review_repair',
                    'final_review_repair_slots':['1/Вт'],
                    'final_review_issues_by_slot':{'1/Вт':[finding]},
                })},progress=lambda e:events.append(e))
            backups=list(drafts.glob('*.final_review_backup.json'))
            self.assertEqual(len(backups),1)
            self.assertEqual(backups[0].read_bytes(),original)
            repair=json.loads((drafts/f"publication_repair_{hashlib.sha256(signature.encode('utf-8')).hexdigest()}.json").read_text(encoding='utf-8'))
            self.assertEqual(repair['drafts']['1/Вт']['post'],post)
            self.assertEqual(repair['drafts']['1/Вт']['issues'],[finding])
            self.assertNotEqual(progress_file.read_bytes(),original)
            canonical=drafts/f'publication_progress_{signature}.json'
            reopened=json.loads(canonical.read_text(encoding='utf-8'))
            self.assertNotIn('1/Вт',reopened['posts'])
            from document_recovery import DocumentDraft
            revoked=DocumentDraft(drafts,signature,'publication_0').load()
            self.assertIs(revoked['valid'],False)
            self.assertTrue(any(e.get('stage')=='final_review_drafts_reopened' for e in events))

    def test_missing_checkpoint_cannot_retain_accepted_claim(self):
        with tempfile.TemporaryDirectory() as home:
            ledger=TaskLedger.open(Path(home),'one')
            ledger.register('publication',['1/Вт','1/Пт'])
            ledger.reconcile(['1/Вт','1/Пт'])
            self.assertTrue(ledger.completed)
            ledger.reconcile(['1/Вт'])
            self.assertFalse(ledger.completed)
            self.assertEqual(ledger.outputs['1/Пт']['status'],'planned')


class WholePlanGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.request='Разработай SMM-стратегию и контент-план для студии керамики, 1 неделя, 2 публикации'
        self.slots=[dict(week=1,day='Вт',topic='Техника лепки глиняных изделий',brief='Практика',goal='Польза',
                         audience='Новички',format='Reels',platform='Instagram'),
                    dict(week=1,day='Пт',topic='Как заменить свечи зажигания автомобиля',brief='Автомеханика',
                         goal='Советы',audience='Автолюбители',format='Карусель',platform='Instagram')]

    def test_unavailable_counter_review_defers_objection_to_content_gates(self):
        events=[]
        def ask(model,system,user,**kwargs):
            if model=='review-a':
                return json.dumps({'issues':[{'id':'1/Пт','dimension':'topic',
                    'reason':'Тема может быть недостаточно связана с целью.'}]},ensure_ascii=False)
            raise TimeoutError('synthetic counter-review timeout')
        result,blocked,available=pg.govern_plan(
            ask,'author',self.request,{},'',self.slots,set(),
            expected_ids=['1/Вт','1/Пт'],review_model=['review-a','review-b'],
            on_event=lambda event,**kw:events.append((event,kw)))
        self.assertTrue(available)
        self.assertEqual(blocked,{})
        self.assertEqual(result,self.slots)
        deferred=[data for event,data in events if event=='plan_review_countercheck_unavailable']
        self.assertEqual(deferred[0]['action'],
                         'defer_objection_to_publication_and_final_document_reviews')

    def test_entire_plan_replanned_prior_to_content_and_accepted_slot_untouched(self):
        events=[]
        alternative={**self.slots[1], 'topic':'Как выбрать фактуру глины для декора',
                     'brief':'Способы создания текстуры', 'audience':'Начинающие мастера'}
        def ask(model,system,user):
            obj=json.loads(user)
            if 'plan' in obj:
                wrong=any('автомобиля' in slot['topic'] for slot in obj['plan'])
                return json.dumps({'issues':[{'id':'1/Пт','dimension':'topic','reason':'Автомеханика не относится к студии керамики'}] if wrong else []})
            if 'candidate' in obj:
                return json.dumps({'issues':[]})
            raise AssertionError('Expected plan audit input')
        with patch.object(pg,'propose_topic_replacement',return_value=[alternative]):
            result,blocked,verified=pg.govern_plan(
                ask,'fake',self.request,{},'',self.slots,{'1/Вт'},
                expected_ids=['1/Вт','1/Пт'],on_event=lambda event,**kw:events.append((event,kw)))
        self.assertTrue(verified)
        self.assertFalse(blocked)
        self.assertEqual(result[0],self.slots[0])
        self.assertEqual(result[1]['topic'],alternative['topic'])
        self.assertEqual(self.slots[1]['topic'],'Как заменить свечи зажигания автомобиля')
        self.assertTrue(any(evt=='plan_replanned' for evt,_ in events))

    def test_reviewer_rejection_blocks_only_pending_and_no_approval_without_review(self):
        def ask(model,system,user):
            obj=json.loads(user)
            return json.dumps({'issues':[{'id':'1/Пт','dimension':'topic','reason':'Публикация не по профилю бизнеса'}]})
        with patch.object(pg,'propose_topic_replacement',return_value=[
            {**self.slots[1], 'topic':'Техника лепки глиняных изделий'}]):
            revised,blocked,verified=pg.govern_plan(ask,'fake',self.request,{},'',self.slots,
                     {'1/Вт'},expected_ids=['1/Вт','1/Пт'])
        self.assertTrue(verified)
        self.assertEqual(revised,self.slots)
        self.assertIn('1/Пт',blocked)
        unreadable,blocked,status=pg.govern_plan(lambda *_:'not JSON','fake',self.request,{},'',self.slots,
                     {'1/Вт'},expected_ids=['1/Вт','1/Пт'])
        self.assertFalse(status)
        self.assertEqual(unreadable,self.slots)

    def test_disputed_topic_veto_needs_agreement_from_distinct_reviewer_family(self):
        calls=[];events=[]
        def ask(model,system,user,**kwargs):
            calls.append(model)
            if model=='review-a':
                return json.dumps({'issues':[{'id':'1/Пт','dimension':'topic',
                    'reason':'Тема не повторяет финальную цель буквально'}]},ensure_ascii=False)
            return '{"issues":[]}'
        result,blocked,available=pg.govern_plan(ask,'author',self.request,{},'',self.slots,set(),
            expected_ids=['1/Вт','1/Пт'],review_model=['review-a','review-b'],
            on_event=lambda event,**kw:events.append((event,kw)))
        self.assertTrue(available)
        self.assertFalse(blocked)
        self.assertEqual(result,self.slots)
        self.assertEqual(calls,['review-a','review-b'])
        self.assertTrue(any(event=='plan_review_disagreement' for event,_ in events))

    def test_matching_independent_reviewers_confirm_a_real_pending_plan_issue(self):
        calls=[]
        def ask(model,system,user,**kwargs):
            calls.append(model)
            return json.dumps({'issues':[{'id':'1/Вт','dimension':'topic',
                'reason':'Тема явно относится к автомобильному ремонту, а не к студии керамики.'}]},ensure_ascii=False)
        result,blocked,available=pg.govern_plan(ask,'author',self.request,{},'',self.slots,
            {'1/Вт'},expected_ids=['1/Вт','1/Пт'],review_model=['review-a','review-b'])
        self.assertTrue(available)
        self.assertIn('1/Вт',blocked)
        self.assertEqual(result,self.slots)
        self.assertEqual(calls,['review-a','review-b'])

    def test_reviewer_cannot_block_exact_slot_count_with_contradictory_count_claim(self):
        events=[]
        slots=[self.slots[0],{**self.slots[1],
                'topic':'Как выбрать фактуру глины для декора',
                'brief':'Показать варианты фактуры', 'audience':'Начинающие мастера'}]
        reason=('В плане содержится только 2 публикации, но в брифе указано, что необходимо '
                'по 2 публикации в неделю, всего 2 публикации. Это означает, что план не соответствует требованиям.')
        def ask(model,system,user):
            return json.dumps({'issues':[{'id':'1/Вт','dimension':'topic','reason':reason}]},ensure_ascii=False)
        _,blocked,available=pg.govern_plan(ask,'random-reviewer',self.request,{},'',slots,set(),
            expected_ids=['1/Вт','1/Пт'],
            on_event=lambda event,**kw:events.append((event,kw)))
        self.assertTrue(available)
        self.assertEqual(blocked,{})
        self.assertTrue(any(event=='plan_review_structural_claim_overruled' for event,_ in events))

    def test_invalid_model_json_gets_one_schema_constrained_retry(self):
        outputs=iter(['not JSON','{"issues":[]}'])
        calls=[]
        def ask(model,system,user,**kwargs):
            calls.append(kwargs)
            return next(outputs)
        _,blocked,verified=pg.govern_plan(ask,'reviewer',self.request,{},'',self.slots,set(),
            expected_ids=['1/Вт','1/Пт'])
        self.assertTrue(verified)
        self.assertFalse(blocked)
        self.assertEqual(len(calls),2)
        self.assertEqual(set(calls[0]['json_schema']['properties']['issues']['items']['properties']['id']['enum']),
                         {'1/Вт','1/Пт'})
        self.assertEqual(calls[0]['json_schema']['properties']['issues']['items']['properties']['dimension']['enum'],
                         ['topic','brief','goal','audience','format','platform'])

    def test_plan_review_contract_excludes_downstream_post_fields(self):
        captured={}
        def ask(model,system,user,**kwargs):
            captured['payload']=json.loads(user)
            captured['schema']=kwargs['json_schema']
            captured['system']=system
            return '{"issues":[]}'
        _,blocked,available=pg.govern_plan(ask,'reviewer',self.request,{},'',self.slots,set(),
            expected_ids=['1/Вт','1/Пт'])
        self.assertTrue(available)
        self.assertEqual(blocked,{})
        contract=captured['payload']['artifact_contract']
        self.assertEqual(contract['artifact_type'],'content_plan')
        self.assertIn('caption',contract['downstream_fields_not_reviewed_here'])
        self.assertIn('cta',contract['downstream_fields_still_required_by_final_delivery'])
        enum=captured['schema']['properties']['issues']['items']['properties']['dimension']['enum']
        self.assertNotIn('caption',enum)
        self.assertIn('downstream-артефакты',captured['system'])
        self.assertIsNone(pg.parse_review(
            '{"issues":[{"id":"1/Вт","dimension":"caption","reason":"missing CTA"}]}',
            {'1/Вт','1/Пт'}))
        self.assertIn('Проверяй слот как единое целое',captured['system'])
        self.assertIn('brief и goal объясняют',captured['system'])
        self.assertIn('Не отклоняй тему лишь потому',captured['system'])

    def test_random_backup_reviewer_recovers_after_primary_returns_invalid_json(self):
        calls=[];backup_flags=[];events=[]
        outputs=iter(['not JSON','still not JSON','{"issues":[]}'])
        def ask(model,system,user,**kwargs):
            calls.append(model)
            backup_flags.append(kwargs.get('backup_reviewer'))
            return next(outputs)
        _,blocked,available=pg.govern_plan(ask,'author',self.request,{},'',self.slots,set(),
            expected_ids=['1/Вт','1/Пт'],review_model=['reviewer-a:7b','reviewer-b:8b'],
            on_event=lambda event,**kw:events.append((event,kw)))
        self.assertTrue(available)
        self.assertEqual(blocked,{})
        self.assertEqual(calls,['reviewer-a:7b','reviewer-a:7b','reviewer-b:8b'])
        self.assertEqual(backup_flags,[False,False,True])
        self.assertTrue(any(event=='plan_review_failover' for event,_ in events))


    def test_candidate_reviewer_unavailable_stops_alternative_fanout(self):
        events=[];calls=[]
        alternatives=[{**self.slots[1], 'topic':f'Тема замены {i}',
                       'brief':'Керамический проект','audience':'Начинающие мастера'}
                      for i in range(5)]
        def ask(model,system,user,**kwargs):
            calls.append((user,kwargs))
            obj=json.loads(user)
            if 'plan' in obj:
                return json.dumps({'issues':[{'id':'1/Пт','dimension':'topic','reason':'Тема вне профиля'}]})
            return 'not JSON'
        with patch.object(pg,'propose_topic_replacement',return_value=alternatives):
            result,blocked,available=pg.govern_plan(ask,'fake',self.request,{},'',self.slots,set(),
                expected_ids=['1/Вт','1/Пт'],
                on_event=lambda event,**kw:events.append((event,kw)))
        self.assertFalse(available)
        self.assertEqual(result,self.slots)
        self.assertIn('1/Пт',blocked)
        # One full-plan review plus two bounded attempts for the first candidate.
        # The other four alternatives must not cause more reviewer calls.
        self.assertEqual(len(calls),3)
        self.assertTrue(all('json_schema' in kwargs for _,kwargs in calls))
        self.assertEqual(calls[-1][1]['json_schema']['properties']['issues']['items']['properties']['id']['enum'],['1/Пт'])
        self.assertEqual(sum(event=='plan_candidate_review_unavailable' for event,_ in events),1)
        self.assertFalse(any(event=='plan_replanned' for event,_ in events))

    def test_blocked_plan_does_not_produce_invalid_content(self):
        request=('Разработай стратегию и контент-план для студии керамики на 1 неделю, '
                 'по 2 публикации в неделю, всего 2 публикации')
        slots=copy.deepcopy(self.slots)
        with tempfile.TemporaryDirectory() as home:
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'_ask',return_value='# Стратегия\n'+('Каналы. Цели. KPI. '*70)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',return_value=slots), \
                 patch.object(ct,'govern_plan',return_value=(slots,{'1/Пт':'Чужая отрасль'},True)), \
                 patch.object(ct,'write_week',side_effect=lambda *a,**k:[good_post(s) for s in a[5]]) as writes, \
                 patch.object(ct,'audit_post',side_effect=lambda a,m,r,k,slot,post,*v:(post,[])), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                result=ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':home})})
            self.assertFalse(result['documents'][-1]['valid'])
            self.assertEqual(result['content_delivery']['checked_posts'],1)
            self.assertEqual(result['content_delivery']['plan_preflight']['unresolved_slots'],['1/Пт'])
            self.assertIn('1/Пт',result['content_delivery']['task_orchestration']['blocked'])
            for call in writes.call_args_list:
                self.assertTrue(all(s['day']!='Пт' for s in call.args[5]))

    def test_full_executor_preflight_preserves_accepted_and_replans_before_writing(self):
        request=('Разработай стратегию и контент-план для студии керамики на 1 неделю, '
                 'по 2 публикации в неделю, всего 2 публикации')
        slots=copy.deepcopy(self.slots)
        accepted_post=good_post(slots[0])
        accepted_post['_editorial_review']=REVIEW_VERSION
        accepted_post['_editorial_policy']=POLICY_VERSION
        class Recorder:
            writes=[]
        def writer(ask,model,request,facts,strategy,slots,previous_topics=(),feedback=''):
            Recorder.writes.extend(s['topic'] for s in slots)
            return [good_post(s) for s in slots]
        def checker(ask,model,req,known,slot,post,*args):
            return post,[]
        def preflight(ask,model,request,brief,strategy,slots,accepted_ids,*,expected_ids,review_model=None,on_event):
            self.assertIn('1/Вт',accepted_ids)
            revised=copy.deepcopy(slots)
            revised[-1]['topic']='Как сформировать фактуру глиняной поверхности'
            on_event('plan_replanned',publication='1/Пт',old_topic=slots[-1]['topic'],
                     new_topic=revised[-1]['topic'])
            return revised,{},True
        with tempfile.TemporaryDirectory() as home:
            drafts=Path(home)/'drafts';drafts.mkdir()
            import hashlib
            signature=hashlib.sha256((home+'\n'+request+'\n1\n2').encode()).hexdigest()
            (drafts/f'publication_progress_{signature}.json').write_text(json.dumps({
                'signature':signature,'posts':{'1/Вт':accepted_post},'completed':1,'total':2}),encoding='utf-8')
            (drafts/f'topic_plan_{signature}.json').write_text(json.dumps({
                'signature':signature,'slots':slots}),encoding='utf-8')
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'_ask',return_value='# Стратегия\n'+('Каналы. Цели. KPI. '*70)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',return_value=slots), \
                 patch.object(ct,'govern_plan',side_effect=preflight), \
                 patch.object(ct,'write_week',side_effect=writer), \
                 patch.object(ct,'audit_post',side_effect=checker), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                result=ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':home})})
            self.assertTrue(result['documents'][-1]['valid'],result['documents'][-1]['reason'])
            self.assertEqual(Recorder.writes,['Как сформировать фактуру глиняной поверхности'])
            self.assertEqual(json.loads((drafts/f'publication_progress_{signature}.json').read_text(encoding="utf-8"))['posts']['1/Вт'],accepted_post)
            self.assertTrue(result['content_delivery']['task_orchestration']['completed'])
            self.assertEqual(result['content_delivery']['plan_preflight']['reviewer_available'],True)
            saved=json.loads((drafts/f'content_delivery_draft_{signature}.json').read_text(encoding="utf-8"))
            self.assertEqual(saved['task_orchestration']['total'],4)

    def test_final_review_retry_runs_targeted_repair_and_keeps_unflagged_slot(self):
        request=('Разработай SMM-стратегию и контент-план для студии керамики, '
                 '1 неделя, 2 публикации в неделю, всего 2 публикации')
        slots=copy.deepcopy(self.slots)
        accepted={}
        for slot in slots:
            post=good_post(slot)
            post['_editorial_review']=REVIEW_VERSION
            post['_editorial_policy']=POLICY_VERSION
            accepted[post['id']]=post
        class StopAfterCalendar(Exception): pass
        with tempfile.TemporaryDirectory() as home:
            drafts=Path(home)/'drafts'; drafts.mkdir()
            import hashlib
            signature=hashlib.sha256((home+'\n'+request+'\n1\n2').encode()).hexdigest()
            progress_file=drafts/f'publication_progress_{signature}.json'
            original=json.dumps({'signature':signature,'posts':accepted,
                                 'completed':2,'total':2},ensure_ascii=False).encode()
            progress_file.write_bytes(original)
            (drafts/f'topic_plan_{signature}.json').write_text(
                json.dumps({'signature':signature,'slots':slots},ensure_ascii=False),encoding='utf-8')
            events=[]
            def stop_at_calendar(*args,**kwargs): raise StopAfterCalendar()
            def repair(*args,**kwargs): return good_post(args[5])
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'produce_checked_strategy',return_value=('# Стратегия',True,'')), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',return_value=slots), \
                 patch.object(ct,'govern_plan',return_value=(slots,{},True)), \
                 patch.object(ct,'write_week',side_effect=AssertionError('retry must repair only cited slot')), \
                 patch.object(ct,'repair_field_targets',return_value=[]), \
                 patch.object(ct,'repair_single',side_effect=repair) as repaired, \
                 patch.object(ct,'audit_post',side_effect=lambda a,m,r,k,slot,post,*v:(post,[])), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False), \
                 patch.object(ct,'week_to_markdown',side_effect=stop_at_calendar), \
                 self.assertRaises(StopAfterCalendar):
                ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({
                    'project_workspace':home,'task_mode':'final_review_repair',
                    'strict_document_recovery':True,
                    'resume_documents':[{'kind':'strategy','content':'# Стратегия','already_saved':False}],
                    'final_review_repair_slots':['1/Вт'],
                    'final_review_issues_by_slot':{'1/Вт':[{'reason':'Недостоверное описание'}]},
                })},progress=lambda e:events.append(e))
            self.assertEqual(repaired.call_count,1)
            self.assertEqual(repaired.call_args.args[5]['day'],'Вт')
            reopened=json.loads(progress_file.read_text(encoding='utf-8'))['posts']
            self.assertEqual(reopened['1/Пт'],accepted['1/Пт'])
            self.assertIn('1/Вт',reopened)
            self.assertTrue(any(e.get('stage')=='targeted_repair_started' and e.get('publication')=='1/Вт'
                                for e in events))
            self.assertFalse(any(e.get('stage')=='batch_attempt' for e in events))

if __name__=='__main__':unittest.main()
