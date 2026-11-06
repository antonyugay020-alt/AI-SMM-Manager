import unittest
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from agent_runtime import validate_document
from unittest.mock import patch
import json
import generic_text_review
import review_qualification
from qualification_fixture import qualification_reply
from generic_text_review import (run_quality_cycle, validate_verdict, CHECKS,
                                 review_text, select_reviewer_models,
                                 validate_model_assignments, list_acceptance_models,
                                 acceptance_model_pool)


def verdict(passed, draft, issues=None):
    return {'pass':passed,'checks':{key:passed for key in CHECKS},
            'issues':issues or []}


class GenericTextReviewTests(unittest.TestCase):
    def setUp(self):
        state=patch.dict(review_qualification._CACHE,{},clear=True);state.start();self.addCleanup(state.stop)
        seed=patch('review_qualification.secrets.randbits',return_value=6901);seed.start();self.addCleanup(seed.stop)
    def test_runtime_reviewer_selection_skips_and_memoizes_bad_json_family(self):
        calls=[]
        def fake_chat(payload,**kwargs):
            calls.append(payload['model'])
            if payload['model']=='gemma3:4b':
                return {'message':{'content':'not-json'}}
            return qualification_reply(payload)
        class OrderedChooser:
            @staticmethod
            def shuffle(values): pass
            @staticmethod
            def choice(values): return values[0]
        pool=['qwen3:8b','gemma3:4b','mistral:7b']
        with patch.dict(generic_text_review._JSON_CAPABILITY_CACHE,{},clear=True), \
             patch('generic_text_review.list_installed_ollama_models',return_value=pool), \
             patch('generic_text_review.request_chat',side_effect=fake_chat):
            first=generic_text_review.select_independent_model('qwen3:8b',chooser=OrderedChooser())
            second=generic_text_review.select_independent_model('qwen3:8b',chooser=OrderedChooser())
        self.assertEqual(first,'mistral:7b')
        self.assertEqual(second,'mistral:7b')
        self.assertEqual(calls,['gemma3:4b','mistral:7b'])

    def test_explicitly_enabled_cloud_candidates_and_scoped_override(self):
        with patch('generic_text_review.list_installed_ollama_models',return_value=['qwen3:8b']), \
             patch.dict('os.environ', {'AI_SMM_CLOUD_MODELS':'groq/qwen/qwen3.8-27b',
                                       'GROQ_API_KEY':'secret',
                                       'AI_SMM_GROQ_FREE_TIER_ACK':'1',
                                       'AI_SMM_ENABLE_FREE_CLOUD_ACCEPTANCE':'1'}, clear=True):
            self.assertEqual(list_acceptance_models(),['qwen3:8b','groq/qwen/qwen3.8-27b'])
            with acceptance_model_pool(['qwen3:8b','groq/qwen/qwen3.8-27b']):
                self.assertEqual(list_acceptance_models(),
                                 ['qwen3:8b','groq/qwen/qwen3.8-27b'])
            self.assertEqual(list_acceptance_models(),['qwen3:8b','groq/qwen/qwen3.8-27b'])

            self.assertEqual(list_acceptance_models(include_cloud=False),['qwen3:8b'])

    def test_validation_uses_only_models_that_passed_json_capability_probe(self):
        with patch.dict('os.environ', {'AI_SMM_ACCEPTANCE_REVIEWER_MODELS':
                                       '["qwen3:8b", "llama3.1:8b"]'}, clear=True):
            self.assertEqual(list_acceptance_models(), ['qwen3:8b', 'llama3.1:8b'])

    def test_invalid_validation_reviewer_pool_fails_closed(self):
        with patch.dict('os.environ', {'AI_SMM_ACCEPTANCE_REVIEWER_MODELS':'not-json'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'JSON-списком'):
                list_acceptance_models()

    def test_generic_output_gate_accepts_short_requested_artifacts(self):
        for request, text in (
            ('Напиши короткий CTA для записи', 'Запишитесь на консультацию.'),
            ('Придумай заголовок', 'Пять способов начать разговор'),
            ('Сделай краткий чек-лист', '1. Проверьте цель.\n2. Подготовьте данные.'),
        ):
            with self.subTest(request=request):
                self.assertEqual(validate_document(request,text),(True,''))

    def test_each_review_pass_randomly_uses_four_distinct_installed_models(self):
        calls=[];probes=[]
        def fake_chat(payload, **kwargs):
            if kwargs.get('stage')=='проверка компетенции модели-рецензента':
                probes.append(payload['model'])
                return qualification_reply(payload)
            else:
                calls.append(payload['model'])
            return {'message':{'content':json.dumps({'pass':True,'issues':[]})}}
        pool=['qwen3:8b','qwen3:14b','gemma3:4b','mistral:7b','deepseek-r1:8b','llama3.1:8b','phi4:14b']
        with patch('generic_text_review.list_installed_ollama_models',return_value=pool), \
             patch('generic_text_review.request_chat', side_effect=fake_chat):
            result=review_text('Напиши короткий CTA','Запишитесь сегодня.',{},model='qwen3:8b')
        self.assertEqual(len(set(calls)),4)
        self.assertEqual(len(set(probes)),4)
        self.assertEqual(set(probes),set(calls))
        self.assertNotIn('qwen3:8b',calls)
        self.assertNotIn('qwen3:14b',calls)
        self.assertTrue(result['independent_model'])
        self.assertEqual(set(result['reviewer_models'].values()),set(calls))
        self.assertEqual(len({name.split(':',1)[0] for name in calls}),4)

    def test_recheck_randomly_assigns_a_new_model_to_each_same_dimension(self):
        pool=['gemma3:4b','mistral:7b','deepseek-r1:8b','llama3.1:8b','phi4:14b']
        first=select_reviewer_models('qwen3:8b',candidates=pool)
        second=select_reviewer_models('qwen3:8b',candidates=pool,previous_reviewers=first)
        self.assertEqual(len(set(first.values())),4)
        self.assertEqual(len(set(second.values())),4)
        for check in CHECKS:
            self.assertNotEqual(first[check],second[check])
            self.assertNotEqual(first[check].split(':',1)[0],second[check].split(':',1)[0])

    def test_model_size_variants_do_not_count_as_distinct_reviewer_families(self):
        pool=['qwen3:8b','gemma3:4b','gemma3:12b','mistral:7b','llama3.1:8b','phi4:14b']
        assignment=select_reviewer_models('qwen3:8b',candidates=pool)
        self.assertEqual(len({name.split(':',1)[0] for name in assignment.values()}),4)
        with self.assertRaisesRegex(ValueError,'минимум 2'):
            select_reviewer_models('qwen3:8b',candidates=[
                'gemma3:4b','gemma3:12b'])

    def test_reviewer_assignment_rejects_reused_or_author_model(self):
        reused={key:'same:latest' for key in CHECKS}
        with self.assertRaises(ValueError):
            validate_model_assignments('qwen3:8b',reused)
        author_reused={'requirements':'qwen3:8b','completeness':'model-b',
                       'factual_grounding':'model-c','format_and_tone':'model-d'}
        with self.assertRaises(ValueError):
            validate_model_assignments('qwen3:8b',author_reused)
        same_family={'requirements':'gemma3:4b','completeness':'gemma3:12b',
                     'factual_grounding':'model-c','format_and_tone':'model-d'}
        self.assertEqual(validate_model_assignments('qwen3:8b',same_family),same_family)
        same_source={**same_family,'factual_grounding':'gemma3:9b'}
        with self.assertRaises(ValueError):validate_model_assignments('qwen3:8b',same_source)

    def test_random_assignment_blocks_when_installed_model_pool_is_too_small(self):
        with self.assertRaisesRegex(ValueError,'минимум 2'):
            select_reviewer_models('qwen3:8b',candidates=['qwen3:8b','gemma3:4b'])

    def test_verdict_rejects_unverified_quote_and_incomplete_dimensions(self):
        with self.assertRaises(ValueError):
            validate_verdict(verdict(False,'Текст',[{'quote':'не из результата','reason':'дефект'}]),'Текст')
        missing={'pass':True,'checks':{'requirements':True},'issues':[]}
        with self.assertRaises(ValueError):
            validate_verdict(missing,'Текст')

    def test_rejected_draft_is_repaired_and_rechecked_before_acceptance(self):
        calls=[]
        def review(request,draft,context,**kwargs):
            calls.append(('review',draft))
            if 'бесплатная' in draft:
                return validate_verdict(verdict(False,draft,[
                    {'quote':'бесплатная доставка','reason':'В исходных данных нет такого условия.'}]),draft)
            return validate_verdict(verdict(True,draft),draft)
        def repair(request,draft,issues,context,**kwargs):
            calls.append(('repair',draft))
            return 'Уточните условия доставки у команды проекта.'
        result=run_quality_cycle('Подготовь короткий текст',
            {'action':'write','instruction':'Подготовь короткий текст',
             'answer':'У нас бесплатная доставка.'},{},reviewer=review,repairer=repair)
        self.assertEqual(result['action'],'write')
        self.assertEqual(result['quality_review']['status'],'PASS')
        self.assertTrue(result['quality_review']['repair_applied'])
        self.assertEqual(calls[0][0],'review')
        self.assertEqual(calls[1][0],'repair')
        self.assertEqual(calls[2][0],'review')

    def test_result_stays_blocked_after_failed_repair(self):
        def review(request,draft,context,**kwargs):
            return validate_verdict(verdict(False,draft,[
                {'quote':draft,'reason':'Не выполнено требование исходного задания.'}]),draft)
        result=run_quality_cycle('Составь отчет',
            {'action':'write','instruction':'Составь отчет','answer':'Неполный отчет.'},
            {},reviewer=review,repairer=lambda *a,**k:'Все еще неполный.')
        self.assertEqual(result['action'],'write_blocked')
        self.assertEqual(result['quality_review']['status'],'BLOCKED')

    def test_randomized_acceptance_records_first_rejected_result_without_repair(self):
        calls=[]
        def review(request,draft,context,**kwargs):
            calls.append('review')
            return validate_verdict(verdict(False,draft,[
                {'quote':draft,'reason':'Не выполнено конкретное требование запроса.'}]),draft)
        def repair(*args,**kwargs):
            calls.append('repair')
            raise AssertionError('fast acceptance must not replay the sample')
        result=run_quality_cycle('Создай одну публикацию',
            {'action':'write','instruction':'Создай одну публикацию','answer':'Черновой результат.'},
            {'randomized_acceptance_run':True},reviewer=review,repairer=repair)
        self.assertEqual(result['action'],'write_blocked')
        self.assertEqual(result['answer'],'Черновой результат.')
        self.assertEqual(result['quality_review']['attempts'],1)
        self.assertTrue(result['quality_review']['first_pass_only'])
        self.assertFalse(result['quality_review']['repair_applied'])
        self.assertEqual(calls,['review'])

    def test_review_outage_fails_closed(self):
        def offline(*args,**kwargs): raise RuntimeError('offline')
        result=run_quality_cycle('Создай материал',
            {'action':'write','instruction':'Создай материал','answer':'Черновой текст.'},
            {},reviewer=offline)
        self.assertEqual(result['action'],'write_blocked')
        self.assertFalse(result['quality_review']['pass'])

    def test_reviewer_instructions_preserve_valid_grammatical_inflections_of_names(self):
        seen={}
        def fake_chat(payload,**kwargs):
            seen['system']=payload['messages'][0]['content']
            user=json.loads(payload['messages'][1]['content'])
            return {'message':{'content':json.dumps({'decisions':{
                item['finding_id']:{'invitation_kind':'none','statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business','source_relation':'entails','verdict':'supported',
                    'source_quote_id':item['allowed_quote_ids'][-1],
                    'reason':'Услуга прямо описана в источнике; падеж имени допустим.'}
                for item in user['findings']}})}}
        with patch('generic_text_review.request_chat',side_effect=fake_chat):
            result=generic_text_review.review_dimension(
                'Проект «Веломир» занимается проверкой тормозов.','В «Веломире» проверят тормоза.',{},
                'factual_grounding','reviewer-model')
        self.assertTrue(result['pass'])
        self.assertIn('грамматическое склонение',seen['system'])
        self.assertIn('падежной формы',seen['system'])


if __name__=='__main__':
    unittest.main()
