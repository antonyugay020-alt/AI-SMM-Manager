from plan_review_fixture import with_plan_review
"""Stage 74: candidate-batch replanning with real 28/30 checkpoint semantics."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks as ct
from semantic_brief import propose_topic_replacement, topic_repeats
from test_stage69_scope import good_post, mini_plan
from semantic_editor import REVIEW_VERSION, POLICY_VERSION


class ProposedTopicOptionsTests(unittest.TestCase):
    def setUp(self):
        self.slot={'week':10,'day':'Пн','format':'Reels','topic':'Растения для открытого грунта',
                   'brief':'Выбор уличных деревьев','goal':'Советы','audience':'Новички'}

    def test_multiple_candidates_skip_duplicate_and_malformed(self):
        data={'alternatives':[
            {'topic':'Правильный уход за комнатными цветами','brief':'Уход','goal':'Советы','audience':'Новички'},
            {'topic':'Совершенно другая тема','brief':'','goal':'Советы','audience':'Новички'},
            {'topic':'Проверка корней комнатных растений перед пересадкой','brief':'Как оценить состояние корней','goal':'Обучение','audience':'Начинающие'},
            {'topic':'Проверка корней комнатных растений перед пересадкой','brief':'Дубликат','goal':'Обучение','audience':'Начинающие'},
            {'topic':'Уход за листьями без неподтверждённых средств','brief':'Бережный осмотр','goal':'Обучение','audience':'Новички'}]}
        ask=lambda *_:json.dumps(data,ensure_ascii=False)
        options=propose_topic_replacement(ask,'mock','Магазин комнатных растений','',{},self.slot,
            ['Правильный уход за комнатными цветами'],[],return_all=True)
        self.assertEqual(len(options),2)
        self.assertIn('корней',options[0]['topic'])
        self.assertEqual(options[1]['week'],10)
        self.assertEqual(options[1]['format'],'Reels')
        self.assertEqual(self.slot['topic'],'Растения для открытого грунта')

    def test_rejected_candidates_are_excluded_on_next_call(self):
        prompts=[]
        def ask(model,sys,user):
            prompts.append(json.loads(user))
            return json.dumps({'alternatives':[
                {'topic':'Уход за листьями без неподтверждённых средств','brief':'Советы','goal':'Помочь','audience':'Новички'},
                {'topic':'Как распознать повреждения листьев перед обработкой','brief':'Осмотр','goal':'Обучить','audience':'Новички'}]},ensure_ascii=False)
        options=propose_topic_replacement(ask,'m','Магазин комнатных растений','',{},self.slot,[],[],
            rejected_topics=['Уход за листьями без неподтверждённых средств'],return_all=True)
        self.assertEqual(len(options),1)
        self.assertIn('повреждения',options[0]['topic'])
        self.assertIn('Уход за листьями без неподтверждённых средств',prompts[0]['rejected_proposals'])

    def test_explicit_user_topic_never_overridden(self):
        request='Обязательно создай публикацию «Растения для открытого грунта».'
        with self.assertRaisesRegex(ValueError,'названа пользователем'):
            propose_topic_replacement(lambda *_:None,'m',request,'',{},self.slot,[],[],return_all=True)

    def test_duplicate_only_candidates_fail_closed_without_replanning(self):
        request='Разработай стратегию и контент-план на 1 неделю, по 2 публикации в неделю, всего 2 публикации'
        with tempfile.TemporaryDirectory() as home:
            drafts=Path(home)/'drafts';drafts.mkdir()
            sig=hashlib.sha256((home+'\n'+request+'\n1\n2').encode()).hexdigest()
            slots=mini_plan(None,None,'',None,{},1,per_week=2)
            slots[1]['topic']='Наружное озеленение не по профилю бизнеса'
            accepted=good_post(slots[0]);accepted['_editorial_review']=REVIEW_VERSION
            accepted['_editorial_policy']=POLICY_VERSION
            pf=drafts/f'publication_progress_{sig[:16]}.json'
            planf=drafts/f'topic_plan_{sig[:16]}.json'
            pf.write_text(json.dumps({'signature':sig,'posts':{accepted['id']:accepted},'total':2,'completed':1}), 'utf8')
            planf.write_text(json.dumps({'signature':sig,'slots':slots}), 'utf8')
            before=slots[1]['topic']
            def llm(*a,**kw):
                return '# Стратегия\n'+('План. Аудитория. Каналы. KPI. '*65)
            def editor(ask,model,req,known,slot,post,*_):
                return (None,['Тема не соответствует специализации бизнеса']) if slot['day']=='Пт' else (post,[])
            import copy
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review(llm)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',side_effect=lambda *a,**k:copy.deepcopy(slots)), \
                 patch.object(ct,'write_week',side_effect=lambda *a,**k:[good_post(x) for x in a[5]]), \
                 patch.object(ct,'write_single',side_effect=lambda *a,**k:good_post(a[5])), \
                 patch.object(ct,'repair_single',side_effect=lambda *a,**k:good_post(a[5])), \
                 patch.object(ct,'audit_post',side_effect=editor), \
                 patch.object(ct,'propose_topic_replacement',return_value=[dict(slots[0])]), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                output=ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':home})})
            self.assertFalse(output['documents'][-1]['valid'])
            self.assertEqual(json.loads(pf.read_text('utf8'))['posts'],{accepted['id']:accepted})
            self.assertEqual(json.loads(planf.read_text('utf8'))['slots'][1]['topic'],before)

    def test_actual_checkpoint_28_of_30_retry_candidates_and_preserve_every_post(self):
        # Use a self-contained fixture shaped like the actual 28/30
        # checkpoint so the suite also runs on the user's Windows computer.
        slots=mini_plan(None,None,'sample','',{},10,per_week=3)
        slots[-3]['topic']='Как выбрать растение для дачного участка'
        slots[-1]['topic']='Как использовать растения для создания зелёного фасада'
        posts={}
        for slot in slots:
            ident=f"{slot['week']}/{slot['day']}"
            if ident in ('10/Пн','10/Пт'):
                continue
            post=good_post(slot)
            post['_editorial_review']=REVIEW_VERSION
            post['_editorial_policy']=POLICY_VERSION
            posts[ident]=post
        progress={'posts':posts,'total':30,'completed':28}
        saved_plan={'slots':slots}
        self.assertEqual(len(progress['posts']),28)
        self.assertEqual(len(saved_plan['slots']),30)
        original_slot_topics={f"{x['week']}/{x['day']}":x['topic'] for x in slots}
        orig_titles={x['topic'] for x in saved_plan['slots']}
        alternatives={
            '10/Пн':[
                slots[0]['topic'], # already accepted => automatically discarded
                'Как организовать безопасное место для комнатного растения рядом с питомцами',
                'Как проверить состояние листьев растения при покупке в магазине'],
            '10/Пт':[
                slots[1]['topic'], # accepted topic; must be discarded
                'Как осмотреть корневую систему комнатного растения перед пересадкой',
                'Как упаковать комнатное растение для поездки домой']}
        self.assertFalse(any(title in orig_titles for title in alternatives['10/Пн'][1:]))
        self.assertFalse(any(title in orig_titles for title in alternatives['10/Пт'][1:]))
        with tempfile.TemporaryDirectory() as home:
            drafts=Path(home)/'drafts';drafts.mkdir()
            task='Разработай стратегию и контент-план для магазина комнатных растений на 10 недель, 3 публикации в неделю, всего 30 публикаций'
            sig=hashlib.sha256((home+'\n'+task+'\n10').encode()).hexdigest()
            (drafts/f'publication_progress_{sig[:16]}.json').write_text(json.dumps({**progress,'signature':sig},ensure_ascii=False),'utf8')
            (drafts/f'topic_plan_{sig[:16]}.json').write_text(json.dumps({**saved_plan,'signature':sig},ensure_ascii=False),'utf8')
            old=progress['posts'].copy()
            call_counts={'first_proposal':0}
            def answer(m,sys,user,stage='-',report=None):
                if 'перепланирование' in stage:
                    d=json.loads(user)
                    ident=f"{d['failed_slot']['week']}/{d['failed_slot']['day']}"
                    return json.dumps({'alternatives':[
                      {'topic':t,'brief':'Конкретные безопасные критерии','goal':'Полезный выбор','audience':'Начинающие любители комнатных растений'}
                      for t in alternatives[ident]]},ensure_ascii=False)
                return '# Стратегия\n'+('Аудитория. Позиционирование. Метрики. Этапы реализации. '*70)
            def editorial(ask,model,req,known,slot,post,*_):
                if post['topic'] in orig_titles and slot['week']==10:
                    return None,['Тема не соответствует специализации бизнеса: нужны комнатные растения']
                if 'рядом с питомцами' in post['topic']:
                    call_counts['first_proposal']+=1
                    return None,['Невозможно подтвердить безопасность для домашних животных']
                if 'корневую систему' in post['topic']:
                    return None,['Рекомендация по корням не учитывает конкретный вид растения']
                return post,[]
            def write_one(ask,model,original,known,strategy,slot,*args,**kw):
                return good_post(slot)
            def existing_plan(ask,model,req,strategy,brief,weeks,**kwargs):
                import copy
                return copy.deepcopy(saved_plan['slots'])
            def write_batch(ask,model,request,facts,strategy,pending,*args,**kwargs):
                self.assertLessEqual(len(pending),2,'Не должна перегенерировать 28 готовых')
                return [good_post(p) for p in pending]
            patches=[patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}),
                patch.object(ct,'_ask',side_effect=with_plan_review(answer)),
                patch.object(ct,'validate_document',return_value=(True,'')),
                patch.object(ct,'plan_slots',side_effect=existing_plan),
                patch.object(ct,'write_week',side_effect=write_batch),
                patch.object(ct,'write_single',side_effect=write_one),
                patch.object(ct,'repair_single',side_effect=lambda *a,**k:good_post(a[5])),
                patch.object(ct,'audit_post',side_effect=editorial),
                patch.object(ct,'copy_too_similar',return_value=False),
                patch.object(ct,'editorial_audit',return_value=[])]
            for p in patches:p.start()
            try:
                result=ct.execute_compound(task,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':home,'task_mode':'resume'})})
            finally:
                for p in reversed(patches):p.stop()
            updated=json.loads((drafts/f'publication_progress_{sig}.json').read_text('utf8'))
            legacy=json.loads((drafts/f'publication_progress_{sig[:16]}.json').read_text('utf8'))
            self.assertEqual(legacy['posts'], old)
            self.assertEqual(legacy['completed'], 28)
            log=[json.loads(line) for line in (drafts/f'publication_diagnostics_{sig}.jsonl').read_text('utf8').splitlines()]
            self.assertEqual(updated['completed'],30,result['documents'][-1]['reason'])
            self.assertTrue(result['documents'][-1]['valid'])
            for ident,post in old.items():
                self.assertEqual(updated['posts'][ident],post,ident)
            newplan=json.loads((drafts/f'topic_plan_{sig[:16]}.json').read_text('utf8'))['slots']
            for ident in alternatives:
                w,day=ident.split('/')
                newslot=next(x for x in newplan if x['week']==int(w) and x['day']==day)
                self.assertEqual(updated['posts'][ident]['topic'],newslot['topic'])
                self.assertNotEqual(newslot['topic'],original_slot_topics[ident])
            stages=[r['stage'] for r in log]
            self.assertEqual(stages.count('topic_scope_recovered'),2)
            self.assertGreaterEqual(stages.count('topic_scope_candidate_rejected'),2)
            self.assertEqual(call_counts['first_proposal'],1)


if __name__=='__main__': unittest.main()
