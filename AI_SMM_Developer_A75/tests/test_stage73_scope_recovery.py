from plan_review_fixture import with_plan_review
"""Stage 73: bounded topic repair after independently confirmed out-of-scope planning."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks as ct
from semantic_brief import propose_topic_replacement
from semantic_editor import REVIEW_VERSION, POLICY_VERSION
from test_stage69_scope import good_post, mini_plan


class TopicScopeRecoveryTests(unittest.TestCase):
    def test_proposal_never_changes_explicit_user_topic(self):
        slot={'week':1,'day':'Пт','topic':'Как защитить рассаду на даче'}
        request='Составь материалы по теме: Как защитить рассаду на даче'
        with self.assertRaisesRegex(ValueError,'названа пользователем'):
            propose_topic_replacement(lambda *a:None,'mock',request,'',{},slot,[],[])

    def test_proposal_must_be_new_and_structurally_complete(self):
        slot={'week':1,'day':'Пт','topic':'Про дачные грядки','format':'Reels'}
        def answer(*args):
            return json.dumps({'topic':'Как правильно ухаживать за домашними папоротниками',
                'brief':'Влажность воздуха и освещение','goal':'Обучить начинающих',
                'audience':'Начинающие цветоводы'},ensure_ascii=False)
        result=propose_topic_replacement(answer,'model','Магазин комнатных растений','',{},slot,
                                         ['Как выбирать офисные растения'],['Тема не соответствует бизнесу'])
        self.assertEqual((result['week'],result['day'],result['format']),(1,'Пт','Reels'))
        self.assertEqual(slot['topic'],'Про дачные грядки')
        self.assertIn('папоротник',result['topic'])
        with self.assertRaisesRegex(ValueError,'повторяет'):
            propose_topic_replacement(answer,'model','Магазин комнатных растений','',{},slot,
                                      [result['topic']],['Тема не соответствует бизнесу'])

    def test_replan_only_failed_slot_and_independently_approve(self):
        request=('Разработай стратегию и контент-план для магазина комнатных растений '
                 'на 1 неделю, 2 публикации в неделю, всего 2 публикации')
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'drafts';folder.mkdir()
            sig=hashlib.sha256((root+'\n'+request+'\n1\n2').encode()).hexdigest()
            slots=mini_plan(None,None,request,'',{},1,per_week=2)
            slots[-1]['topic']='Как выращивать деревья в открытом грунте'
            original=good_post(slots[0]);original['_editorial_review']=REVIEW_VERSION
            original['_editorial_policy']=POLICY_VERSION
            (folder/f'publication_progress_{sig[:16]}.json').write_text(json.dumps({
                'signature':sig,'posts':{original['id']:original},'total':2,'completed':1}),encoding='utf-8')
            (folder/f'topic_plan_{sig[:16]}.json').write_text(json.dumps({
                'signature':sig,'slots':slots}),encoding='utf-8')
            initial_topic=slots[-1]['topic']
            replacement={**slots[-1], 'topic':'Как подобрать влажность для комнатных растений',
                         'brief':'Выбор условий для ухода'}
            def editorial(ask,model,req,known,slot,post,*_):
                if post['topic']==initial_topic:
                    return None,['Тема не соответствует специализации бизнеса: только комнатные растения']
                return post,[]
            def write_batch(ask,model,req,facts,strat,slot_subset,*args,**kwargs):
                return [good_post(s) for s in slot_subset]
            def llm(m,sys,usr,stage='-',report=None):
                return '# Стратегия\n'+('Цели. Аудитория. План. Измерение KPI. '*60)
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review(llm)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',side_effect=mini_plan), \
                 patch.object(ct,'write_week',side_effect=write_batch), \
                 patch.object(ct,'write_single',side_effect=lambda *a,**kw:good_post(a[5])), \
                 patch.object(ct,'repair_single',side_effect=lambda *a,**kw:good_post(a[5])), \
                 patch.object(ct,'audit_post',side_effect=editorial), \
                 patch.object(ct,'propose_topic_replacement',return_value=replacement) as planner, \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                result=ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':root})})
            self.assertGreaterEqual(planner.call_count,1)
            new=json.loads((folder/f'publication_progress_{sig}.json').read_text(encoding="utf-8"))
            self.assertEqual(new['completed'],2)
            self.assertEqual(new['posts'][original['id']],original)
            self.assertEqual(new['posts'][slots[-1]['week'].__str__()+'/'+slots[-1]['day']]['topic'],replacement['topic'])
            plan=json.loads((folder/f'topic_plan_{sig[:16]}.json').read_text(encoding="utf-8"))
            self.assertEqual(plan['slots'][-1]['topic'],replacement['topic'])
            self.assertTrue(result['documents'][-1]['valid'],result['documents'][-1]['reason'])
            diag=(folder/f'publication_diagnostics_{sig}.jsonl').read_text(encoding="utf-8")
            self.assertIn('topic_scope_recovered',diag)

if __name__=='__main__':unittest.main()
