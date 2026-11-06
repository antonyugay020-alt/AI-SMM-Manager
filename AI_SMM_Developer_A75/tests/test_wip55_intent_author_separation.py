"""Architectural tests: routing is not a generated artifact."""
import json
import unittest
from unittest.mock import patch

from agent_runtime import route_text_intent
from text_workflow import run_text_task


class IntentAuthorSeparationTests(unittest.TestCase):
    def test_router_has_no_permission_to_supply_an_artifact(self):
        def decision(answer):
            return {'message':{'content':json.dumps({
                'action':'write','answer':answer,'start':None,'end':None,
                'content_contract':{'mode':'factual'}})}}
        with patch('agent_runtime.request_chat',return_value=decision('user instruction copied')):
            with self.assertRaisesRegex(ValueError,'Маршрутизатор попытался'):
                route_text_intent('Напиши пост',{})
        with patch('agent_runtime.request_chat',return_value=decision('')):
            routed=route_text_intent('Напиши пост',{})
            self.assertEqual(routed['action'],'write')
            self.assertEqual(routed['answer'],'')

    def test_router_contract_correction_discards_draft_and_does_not_authorize_content(self):
        invalid={'action':'write','answer':'Непроверенный черновик маршрутизатора',
                 'start':None,'end':None,'content_contract':{'mode':'factual'}}
        valid={**invalid,'answer':''}
        def response(value):
            return {'message':{'content':json.dumps(value,ensure_ascii=False)}}
        with patch('agent_runtime.request_chat',side_effect=[response(invalid),response(valid)]) as ask:
            routed=route_text_intent('Напиши пост',{})
        self.assertEqual(ask.call_count,2)
        self.assertEqual(routed['action'],'write')
        self.assertEqual(routed['answer'],'')
        self.assertNotIn('Непроверенный черновик',str(routed))

    def test_router_does_not_accept_second_invalid_response_or_switch_action(self):
        invalid={'action':'write','answer':'Черновик','start':None,'end':None,
                 'content_contract':{'mode':'factual'}}
        changed={**invalid,'action':'respond','answer':'Ответ'}
        for final in (invalid,changed):
            def response(value):
                return {'message':{'content':json.dumps(value,ensure_ascii=False)}}
            with patch('agent_runtime.request_chat',side_effect=[response(invalid),response(final)]) as ask:
                with self.assertRaisesRegex(ValueError,'Маршрутизатор попытался'):
                    route_text_intent('Напиши пост',{})
                self.assertEqual(ask.call_count,2)

    def test_router_and_author_are_separate_for_real_standalone_flow(self):
        author_output='Полезный готовый пост. Сравните доступные материалы и уточните детали.'
        calls=[]
        def route(request, context, model, report):
            calls.append('route')
            return {'action':'write','answer':'','content_contract':{'mode':'factual'},
                    'instruction':request}
        def write(request, context, model, report):
            calls.append('author')
            return {'action':'write','answer':author_output,
                    'content_contract':{'mode':'factual'},'instruction':request}
        def review(request, initial, context, model, report):
            calls.append('review')
            self.assertEqual(initial['answer'],author_output)
            return {'action':'write','answer':initial['answer']}
        with patch('generic_text_review.select_reviewer_models',return_value=([],['judge:1','judge:2'])), \
             patch('agent_runtime.route_text_intent',side_effect=route), \
             patch('agent_runtime.write_requested_artifact',side_effect=write):
            result=run_text_task('Напиши один пост',{},model='writer:1',quality_cycle=review)
        self.assertEqual(calls,['route','author','review'])
        self.assertEqual(result['answer'],author_output)

    def test_nonwriting_decision_does_not_invoke_author(self):
        def route(request, context, model, report):
            return {'action':'respond','answer':'Это объяснение','content_contract':None,
                    'instruction':request}
        with patch('agent_runtime.route_text_intent',side_effect=route), \
             patch('agent_runtime.write_requested_artifact',side_effect=AssertionError('must not run')):
            result=run_text_task('Объясни разницу',{},model='writer:1',
                quality_cycle=lambda r,i,s,model,report:i)
        self.assertEqual(result['action'],'respond')

if __name__=='__main__':
    unittest.main()
