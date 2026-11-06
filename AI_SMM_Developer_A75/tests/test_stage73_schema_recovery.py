from plan_review_fixture import with_plan_review
"""Stage 73: schema-safe normalization and resuming 28/30 from rejected drafts."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks as ct
from publication_writer import normalize_model_post, validate_single, repair_fields
from test_stage69_scope import good_post, mini_plan
from semantic_editor import POLICY_VERSION, REVIEW_VERSION


class ModelSchemaNormalizationTests(unittest.TestCase):
    def test_release_version_visible(self):
        from version_info import APP_VERSION, STAGE_NUMBER, BUILD_LABEL
        self.assertGreaterEqual(STAGE_NUMBER,73)
        self.assertTrue(APP_VERSION.startswith(f'{STAGE_NUMBER}.'))
        self.assertIn(f'Stage {STAGE_NUMBER} · v{APP_VERSION}',BUILD_LABEL)

    def test_audience_list_is_normalized_without_fabricating_content(self):
        original={'id':'10/Пн', 'topic':'Как выбрать комнатное растение',
                  'audience':[' Начинающие любители ', ' Владельцы растений '],
                  'caption':'Не переписывать это поле'}
        normalized, changed=normalize_model_post(original)
        self.assertEqual(changed,('audience',))
        self.assertEqual(normalized['audience'],'Начинающие любители; Владельцы растений')
        self.assertEqual(normalized['id'],original['id'])
        self.assertEqual(normalized['topic'],original['topic'])
        self.assertEqual(normalized['caption'],original['caption'])
        self.assertIsInstance(original['audience'],list)

    def test_missing_audience_remains_invalid(self):
        slot={'week':10,'day':'Пт','topic':'Зелёные уголки в квартире','format':'Карусель'}
        for invalid in ([], [''], [' ','\n'], [{'name':'Люди'}], None):
            with self.subTest(invalid=invalid):
                p=good_post(slot)
                p['audience']=invalid
                normalized,_=normalize_model_post(p)
                self.assertIn('пустое поле audience',validate_single(normalized,slot))

    def test_scenes_convert_without_hallucinating_media(self):
        post={'visual':{'format':'Stories','scenes':[
            {'scene':1,'image':'Фотография горшка','text':'Проверьте влажность'},
            {'scene':2,'image':'Крупный план почвы','text':'Поливайте с учётом вида'}]}}
        normalized,changed=normalize_model_post(post)
        self.assertEqual(changed,('visual',))
        self.assertIn('Фотография горшка',normalized['visual'])
        self.assertIn('Поливайте с учётом вида',normalized['visual'])
        self.assertNotIn('у нас есть',normalized['visual'])

    def test_field_patch_requires_text_replacements(self):
        slot={'week':10,'day':'Пн','topic':'Уход за растением','format':'Reels'}
        post=good_post(slot)
        with self.assertRaises(ValueError):
            repair_fields(lambda *args:json.dumps({'replacements':{'P001':['Новички','Домашние цветоводы']}}),
                'm','',{},'',slot,post,['пустое поле audience'])

    def test_normalization_does_not_bypass_editorial_or_business_risks(self):
        from editorial_guard import editorial_risks
        slot={'week':10,'day':'Пн','topic':'Уход за растением','format':'Reels'}
        post=good_post(slot)
        post.update(audience=['Новички'],cta='У нас вы можете заказать профессиональный дизайн.',)
        normalized,_=normalize_model_post(post)
        self.assertEqual(validate_single(normalized,slot),[])
        request='Не придумывай услуги и не обещай дополнительные услуги.'
        self.assertTrue(editorial_risks(normalized,request))

    def test_checkpoint_28_of_30_recovered_without_rewriting(self):
        request=('Разработай стратегию и контент-план для магазина комнатных растений '
                 'на 10 недель, 3 публикации в неделю, всего 30 публикаций')
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'drafts';folder.mkdir()
            sig=hashlib.sha256((root+'\n'+request+'\n10').encode()).hexdigest()
            slots=mini_plan(None,None,request,'',{},10,per_week=3)
            initial={}
            for slot in slots[:28]:
                post=good_post(slot)
                post['_editorial_review']=REVIEW_VERSION
                post['_editorial_policy']=POLICY_VERSION
                initial[post['id']]=post
            (folder/f'publication_progress_{sig[:16]}.json').write_text(
                json.dumps({'signature':sig,'posts':initial,'total':30,'completed':28}),encoding='utf-8')
            (folder/f'topic_plan_{sig[:16]}.json').write_text(
                json.dumps({'signature':sig,'slots':slots}),encoding='utf-8')
            drafts={}
            for slot in slots[28:]:
                post=good_post(slot)
                post['audience']=['Цветоводы','Люди, интересующиеся растениями']
                drafts[post['id']]={'post':post,'issues':['пустое поле audience']}
            (folder/f'publication_repair_{sig[:16]}.json').write_text(
                json.dumps({'signature':sig,'drafts':drafts}),encoding='utf-8')
            def llm(model,system,user,stage='-',report=None):
                if 'независимая редактура' in stage:
                    return json.dumps({'approved':True,'issues':[]})
                return '# Стратегия\n'+('Цели. Аудитория. План. Измерение KPI. '*60)
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(ct,'_ask',side_effect=with_plan_review(llm)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), \
                 patch.object(ct,'plan_slots',side_effect=mini_plan), \
                 patch.object(ct,'write_week',side_effect=AssertionError('Не нужно пересоздавать черновики')), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'copy_too_similar',return_value=False):
                result=ct.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':root})})
            new=json.loads((folder/f'publication_progress_{sig}.json').read_text(encoding="utf-8"))
            self.assertEqual(new['completed'],30)
            self.assertTrue(result['documents'][-1]['valid'],result['documents'][-1]['reason'])
            self.assertEqual(result['content_delivery']['checked_posts'],30)
            for key,post in initial.items():
                self.assertEqual(post,new['posts'][key])
            self.assertEqual(new['posts'][slots[-1]['week'].__str__()+'/'+slots[-1]['day']]['audience'],
                             'Цветоводы; Люди, интересующиеся растениями')
            # The historical prefix-addressed file is deliberately read-only.
            # Recovery must produce an empty *canonical* repair checkpoint,
            # while preserving the original legacy record for audit/migration.
            legacy_repairs=json.loads((folder/f'publication_repair_{sig[:16]}.json').read_text(encoding='utf-8'))
            self.assertEqual(set(legacy_repairs['drafts']),{p['id'] for p in [good_post(slot) for slot in slots[28:]]})
            canonical=folder/f"publication_repair_{hashlib.sha256(sig.encode('utf-8')).hexdigest()}.json"
            repairs=json.loads(canonical.read_text(encoding='utf-8'))
            self.assertEqual(repairs['signature'],sig)
            self.assertEqual(repairs['drafts'],{})
            trace=(folder/f'publication_diagnostics_{sig}.jsonl').read_text(encoding="utf-8")
            self.assertEqual(trace.count('schema_checkpoint_recovered'),2)
            self.assertIn('documents_ready_for_final_review',trace)
            self.assertNotIn('"stage": "task_done"',trace)

if __name__=='__main__':
    unittest.main()
