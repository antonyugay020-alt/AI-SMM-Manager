import json
import unittest
from unittest.mock import patch
import compound_tasks as ct

class EvidenceChallengeTests(unittest.TestCase):
    def check(self, decisions, challenge, brief='Наша доставка работает ночью. Цена пока неизвестна.'):
        with patch.object(ct, '_ask', side_effect=[json.dumps({'decisions':decisions},ensure_ascii=False), challenge]) as mock:
            result=ct._unanswered_critical_questions(['Какова цена?'], brief, model='test-model')
            return result, mock.call_count

    def test_unrelated_real_quote_cannot_clear_question(self):
        result,count=self.check([{'answered':True,'evidence_quote':'доставка работает ночью'}], '{"sufficient":false}')
        self.assertEqual(['Какова цена?'],result);self.assertEqual(2,count)

    def test_true_relevant_quote_can_clear(self):
        result,count=self.check([{'answered':True,'evidence_quote':'Стоимость услуги — 100 тенге.'}], '{"sufficient":true}', 'Стоимость услуги — 100 тенге.')
        self.assertEqual([],result);self.assertEqual(2,count)

    def test_challenge_failure_fails_closed(self):
        result,_=self.check([{'answered':True,'evidence_quote':'доставка работает ночью'}], 'garbled')
        self.assertEqual(['Какова цена?'],result)

    def test_no_evidence_does_not_call_second_model(self):
        result,count=self.check([{'answered':True,'evidence_quote':'несуществующий фрагмент'}], '{"sufficient":true}')
        self.assertEqual(['Какова цена?'],result);self.assertEqual(1,count)
