import json
import unittest
from unittest.mock import patch
import windows_acceptance as acceptance

class Response:
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def read(self,*a):return json.dumps(self.data).encode('utf-8')

class WindowsAcceptanceV2Tests(unittest.TestCase):
    def setUp(self):
        self.before=dict(acceptance.RESULT['checks'])
    def tearDown(self):
        acceptance.RESULT['checks'].clear()
        acceptance.RESULT['checks'].update(self.before)
    def test_qwen3_explicitly_disables_thinking_and_accepts_utf8_json(self):
        calls=[]
        def urlopen(req,timeout=None):
            url=req if isinstance(req,str) else req.full_url
            if url.endswith('/api/version'):return Response({'version':'0.13.0'})
            if url.endswith('/api/tags'):return Response({'models':[{'name':'qwen3:8b'}]})
            payload=json.loads(req.data.decode('utf-8'))
            calls.append(payload)
            if isinstance(payload.get('format'),dict):
                if 'segment_decisions' in payload['format'].get('properties',{}):
                    ids=payload['format']['properties']['segment_decisions']['required']
                    decisions={ident:'not_business_claim' for ident in ids}
                    content=json.dumps({'segment_decisions':decisions})
                    return Response({'message':{'content':content}, 'done_reason':'stop','eval_count':30})
                return Response({'message':{'content':'{"pass":true,"issues":[]}'}, 'done_reason':'stop','eval_count':20})
            if payload.get('format')=='json':
                return Response({'message':{'content':'{"action":"respond","answer":"готово"}'}, 'done_reason':'stop','eval_count':18})
            return Response({'message':{'content':'ГОТОВО'}, 'done_reason':'stop','eval_count':8})
        with patch.object(acceptance.urllib.request,'urlopen',side_effect=urlopen):
            acceptance.ollama_probe()
        self.assertEqual(acceptance.RESULT['checks']['ollama_roundtrip']['status'],'PASS')
        self.assertEqual(acceptance.RESULT['checks']['ollama_structured_json']['status'],'PASS')
        self.assertEqual(acceptance.RESULT['checks']['ollama_constrained_schema']['status'],'PASS')
        self.assertEqual(acceptance.RESULT['checks']['ollama_inventory_map_schema']['status'],'PASS')
        self.assertEqual(len(calls),4)
        self.assertTrue(all(x['think'] is False and x['options']['num_predict']>=128 for x in calls))
        self.assertIsInstance(calls[2]['format'],dict)
        self.assertIn('issues',calls[2]['format']['properties'])
        self.assertIn('segment_decisions',calls[3]['format']['properties'])
    def test_empty_content_cannot_be_misreported_as_success(self):
        def urlopen(req,timeout=None):
            url=req if isinstance(req,str) else req.full_url
            if url.endswith('/api/version'):return Response({'version':'0.13.0'})
            if url.endswith('/api/tags'):return Response({'models':[{'name':'qwen3:8b'}]})
            return Response({'message':{'content':'','thinking':'reasoning only'}, 'done_reason':'length','eval_count':24})
        with patch.object(acceptance.urllib.request,'urlopen',side_effect=urlopen):
            acceptance.ollama_probe()
        self.assertEqual(acceptance.RESULT['checks']['ollama_roundtrip']['status'],'FAIL')
        self.assertEqual(acceptance.RESULT['checks']['ollama_roundtrip']['content_length'],0)
        self.assertEqual(acceptance.RESULT['checks']['ollama_roundtrip']['thinking_length'],len('reasoning only'))
