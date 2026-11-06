"""Regression from Windows VALIDATION_RESULTS(20261102-152235):
A drafting request cannot be downgraded into an unreviewed chat response.
"""
import json
import unittest
from unittest.mock import patch
from agent_runtime import route_text_intent
from text_workflow import run_text_task


def response(action, answer):
    return {'message':{'content':json.dumps({'action':action,'answer':answer,
        'start':None,'end':None,'content_contract':{'mode':'factual'}}, ensure_ascii=False)}}


class WindowsStandaloneRoutingRegression(unittest.TestCase):
    def test_actual_telegram_brief_reaches_author_and_review(self):
        request=('Подготовь одну публикацию для Telegram для проекта «Понятный компьютер». '
                 'Подтверждённая услуга: настройка программ и перенос файлов по согласованию. '
                 'Создай один завершённый текст публикации и заверши вопросом.')
        events=[]
        def author(message,context,model,report):
            events.append('author')
            return {'action':'write','answer':'Проверяемый авторский текст.','content_contract':{'mode':'factual'}}
        def review(request,initial,context,model,report):
            events.append('review')
            self.assertEqual(initial['answer'],'Проверяемый авторский текст.')
            return {'action':'write','answer':initial['answer'],'quality_review':{'pass':True}}
        report=[]
        with patch('agent_runtime.request_chat',return_value=response('respond','Черновик маршрутизатора, не проверять и не публиковать')), \
             patch('agent_runtime.write_requested_artifact',side_effect=author):
            value=run_text_task(request,{},model='gemma3:4b', quality_cycle=review,progress=report.append)
        self.assertEqual(events,['author','review'])
        self.assertEqual(value['action'],'write')
        self.assertNotIn('Черновик маршрутизатора',str(value))
        self.assertIn('routing_action_corrected',[item.get('stage') for item in report])

    def test_actual_instagram_brief_cannot_turn_into_unverified_reply(self):
        request='Напиши один готовый пост для Instagram для мастерской ремонта обуви. Создай текст публикации.'
        with patch('agent_runtime.request_chat',return_value=response('respond','Черновик поста')):
            value=route_text_intent(request,{},model='mistral:7b')
        self.assertEqual(value['action'],'write')
        self.assertEqual(value['answer'],'')
        self.assertEqual(value['instruction'],request)

    def test_direct_information_question_stays_respond(self):
        with patch('agent_runtime.request_chat') as api:
            from agent_runtime import route_text_intent
            with patch('agent_runtime.conversational_answer',return_value={'action':'respond','answer':'Разница.'}) as answer:
                val=route_text_intent('Объясни разницу между KPI и метрикой',{})
        self.assertEqual(val['action'],'respond')
        answer.assert_called_once()
        api.assert_not_called()

    def test_explicit_draft_request_does_not_inherit_unverified_fictional_permission(self):
        request='Напиши пост о мастерской'
        raw={'action':'respond','answer':'Пост','start':None,'end':None,
             'content_contract':{'mode':'fictional','permission_quote':'выдумай услуги'}}
        with patch('agent_runtime.request_chat',return_value={'message':{'content':json.dumps(raw,ensure_ascii=False)}}):
            value=route_text_intent(request,{})
        self.assertEqual(value['content_contract'],{'mode':'factual'})

if __name__=='__main__': unittest.main()
