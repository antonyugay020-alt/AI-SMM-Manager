from plan_review_fixture import with_plan_review
"""Stage 72: field-safe editorial recovery and 26/30 checkpoint preservation."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks as ct
from publication_writer import repair_fields, repair_field_targets
from test_stage69_scope import good_post, mini_plan
from semantic_editor import POLICY_VERSION, REVIEW_VERSION


class Stage72RecoveryTests(unittest.TestCase):
    def test_kpi_patch_does_not_rewrite_valid_caption_or_slot(self):
        slot={'week':4,'day':'Пн','topic':'Выбор почвы','format':'Карусель'}
        original=good_post(slot)
        original['kpi']='Рост +25%'
        seen=[]
        def ask(model,system,user):
            payload=json.loads(user)
            seen.append(payload)
            return json.dumps({'replacements':{row['id']:('Сохранения публикации' if row['field']=='kpi' else 'Считать сохранения в Insights') for row in payload['revision_spans']}},ensure_ascii=False)
        result=repair_fields(ask,'model','Не предоставлена исходная статистика',{},'',slot,original,
                             ['Числовой прирост KPI не обоснован: исходная статистика не предоставлена'])
        self.assertEqual((result['id'],result['topic']),('4/Пн','Выбор почвы'))
        self.assertEqual(result['caption'],original['caption'])
        self.assertEqual(result['visual'],original['visual'])
        self.assertEqual(result['kpi'],'Сохранения публикации')
        self.assertEqual(result['measurement'],'Считать сохранения в Insights')
        self.assertEqual({row['field'] for row in seen[0]['revision_spans']},{'kpi','measurement'})

    def test_no_forged_identity_or_unsafe_field_changes(self):
        slot={'week':2,'day':'Ср','topic':'Подбор света','format':'Stories'}
        original=good_post(slot)
        self.assertEqual(repair_field_targets(['Тема публикации не соответствует утверждённому плану']),())
        with self.assertRaises(ValueError):
            repair_fields(lambda *a:json.dumps({'changes':{'topic':'Новая тема'}}),
                          'model','','','',slot,original,['текст публикации короче 220 символов'])

    def test_does_not_approve_invalid_payload(self):
        slot={'week':2,'day':'Пт','topic':'Подбор света','format':'Stories'}
        original=good_post(slot)
        with self.assertRaises(ValueError):
            repair_fields(lambda *a:json.dumps({'changes':{'id':'99/Пт'}}),
                          'm','','','',slot,original,['текст публикации короче 220 символов'])
        with self.assertRaisesRegex(ValueError,'не изменила'):
            repair_fields(lambda *a:json.dumps({'replacements':{'P001':original['caption']}}),
                          'm','','','',slot,original,['текст публикации короче 220 символов'])

    def test_twenty_six_of_thirty_survives_recovery(self):
        request=('Разработай стратегию и контент-план для магазина комнатных растений '
                 'на 10 недель, 3 публикации в неделю, всего 30 публикаций')
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'drafts';folder.mkdir()
            sig=hashlib.sha256((root+'\n'+request+'\n10').encode('utf-8')).hexdigest()
            slots=mini_plan(None,None,request,'',{},10,per_week=3)
            initial={}
            for slot in slots[:26]:
                post=good_post(slot)
                post['_editorial_review']=REVIEW_VERSION
                post['_editorial_policy']=POLICY_VERSION
                initial[post['id']]=post
            (folder/f'publication_progress_{sig[:16]}.json').write_text(
                json.dumps({'signature':sig,'posts':initial,'total':30,'completed':26}),encoding='utf-8')
            (folder/f'topic_plan_{sig[:16]}.json').write_text(
                json.dumps({'signature':sig,'slots':slots}),encoding='utf-8')
            patch_count=[]
            def llm(model,sys,user,stage='-',report=None):
                if 'правка полей' in stage:
                    payload=json.loads(user)
                    slot=payload['approved_slot']
                    patch_count.append(f"{slot['week']}/{slot['day']}")
                    return json.dumps({'replacements':{row['id']:good_post(slot)[row['field']] for row in payload['revision_spans']}},ensure_ascii=False)
                if 'независимая редактура' in stage:
                    return json.dumps({'approved':True,'issues':[]})
                return '# Стратегия\n'+('Каналы и цели. Целевая аудитория. KPI, бюджет, метод измерения. '*40)
            def failed_batches(ask,model,req,facts,strategy,slots,previous_topics=None,feedback=''):
                out=[]
                for slot in slots:
                    post=good_post(slot)
                    post['caption']='Слишком кратко.'
                    out.append(post)
                return out
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review(llm)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',side_effect=mini_plan), \
                 patch.object(ct,'write_week',side_effect=failed_batches), \
                 patch.object(ct,'copy_too_similar',return_value=False), \
                 patch.object(ct,'editorial_audit',return_value=[]):
                result=ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':root})})
            new=json.loads((folder/f'publication_progress_{sig}.json').read_text(encoding="utf-8"))
            self.assertEqual(new['completed'],30)
            self.assertTrue(result['documents'][-1]['valid'],result['documents'][-1]['reason'])
            self.assertEqual(result['content_delivery']['checked_posts'],30)
            self.assertEqual(len(patch_count),4)
            for key,post in initial.items():self.assertEqual(post,new['posts'][key])
            diagnostics=(folder/f'publication_diagnostics_{sig}.jsonl').read_text(encoding='utf-8')
            self.assertIn('field_patch_started',diagnostics)
            self.assertIn('documents_ready_for_final_review',diagnostics)
            self.assertNotIn('"stage": "task_done"',diagnostics)

if __name__=='__main__': unittest.main()
