import unittest
from agent_runtime import parse_task_plan_response, validate_task_plan

VALID='{"action":"respond","answer":"Готово","start":null,"end":null,"content_contract":{"mode":"factual"}}'
class PlanFenceTests(unittest.TestCase):
    def test_plain_and_fenced_are_equivalent(self):
        self.assertEqual(validate_task_plan(parse_task_plan_response(VALID)),validate_task_plan(parse_task_plan_response('```json\n'+VALID+'\n```')))
    def test_prose_is_rejected(self):
        with self.assertRaises(ValueError): parse_task_plan_response('Вот план: '+VALID)
    def test_multiple_objects_rejected(self):
        with self.assertRaises(ValueError): parse_task_plan_response(VALID+'\n'+VALID)
    def test_fenced_invalid_contract_rejected(self):
        with self.assertRaises(ValueError): validate_task_plan(parse_task_plan_response('```json\n{"action":"write"}\n```'))
    def test_bom(self):
        self.assertEqual(parse_task_plan_response('\ufeff'+VALID)['action'],'respond')
if __name__=='__main__': unittest.main()
