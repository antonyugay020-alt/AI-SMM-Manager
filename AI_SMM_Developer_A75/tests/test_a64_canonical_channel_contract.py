import unittest
from unittest.mock import patch

import compound_tasks
from platform_registry import explicit_platforms, platform_label
from publication_writer import week_to_markdown
from semantic_brief import analyze


class CanonicalChannelContractTests(unittest.TestCase):
    def test_literal_user_channel_overrides_model_misclassification(self):
        result=analyze(lambda *args: '{"channels":"Telegram","audience":"adults"}',
                       'qwen3:8b','План Instagram на неделю')
        self.assertEqual(result['channels'],['Instagram'])
        self.assertEqual(result['channels_basis'],'explicit')

    def test_aliases_are_canonicalized_in_user_mention_order(self):
        self.assertEqual(explicit_platforms('Посты для Telegram и Инстаграм, затем TikTok'),
                         ['Telegram','Instagram','TikTok'])

    def test_multiple_channels_are_clarified_before_document_generation(self):
        request='Создай стратегию и контент-план для Instagram и Telegram.'
        # The analyzer can miss or misclassify a literal requirement. The
        # orchestration boundary must recover it from the original request.
        brief={'channels':['Telegram'],'channels_basis':'recommendation'}
        with patch.object(compound_tasks,'extract_facts',return_value={}), \
             patch.object(compound_tasks,'analyze',return_value=brief):
            result=compound_tasks.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':None})})
        self.assertEqual(result['action'],'compound_clarify')
        self.assertIn('Instagram и Telegram',result['answer'])
        self.assertEqual(result.get('documents',[]),[])

    def test_recommended_channel_is_labeled_as_a_recommendation_in_calendar(self):
        text=week_to_markdown(
            [{'id':'1/Вт','topic':'Тема','visual':'Кадр','caption':'Текст','cta':'Действие',
              'kpi':'Ответы','measurement':'Подсчёт','audience':'Взрослые','materials':'Фото'}],
            [{'week':1,'day':'Вт','platform':'Instagram','platform_basis':'recommendation',
              'format':'Карусель','goal':'Объяснить'}])
        self.assertIn('Instagram (предложение AI)',text)


if __name__=='__main__':
    unittest.main()
