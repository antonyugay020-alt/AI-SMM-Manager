import json
import unittest
from unittest.mock import patch

from agent_runtime import plan_task


class AgentRuntimeProviderRouteR38Tests(unittest.TestCase):
    def test_single_task_dispatch_uses_configured_shared_provider_transport(self):
        report=[]
        answer={'action':'write','instruction':'Напиши пост','answer':'Готовый пост','start':None,'end':None,'content_contract':{'mode':'factual'}}
        with patch('agent_runtime.request_chat',return_value={
                'message':{'content':json.dumps(answer,ensure_ascii=False)}}) as transport:
            result=plan_task('Напиши пост для проекта',{'project':'synthetic'},
                             model='openai/gpt-6-luna',report=lambda *a,**k:report.append((a,k)))
        payload=transport.call_args.args[0]
        self.assertEqual(payload['model'],'openai/gpt-6-luna')
        self.assertEqual(transport.call_args.kwargs['stage'],
                         'маршрутизация и создание отдельного материала')
        self.assertEqual(result['action'],'write')
        self.assertEqual(result['instruction'],'Напиши пост для проекта')


if __name__=='__main__':
    unittest.main()
