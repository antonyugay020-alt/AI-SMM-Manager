import unittest
from editorial_evidence_matrix import disproved_user_stated_fact
from independent_acceptance_editor import _nonblocking_editor_false_positive, parse_verdict

class ReviewerCalibrationA12(unittest.TestCase):
    def test_positive_user_booking_fact_refutes_false_accusation(self):
        request='Мастерская проводит диагностику и принимает клиентов по предварительной записи.'
        issue={'type':'invented_fact','document':'calendar',
               'quote':'Запишитесь на диагностику в мастерскую.',
               'reason':'Фраза «Запишитесь на диагностику» не подтверждена запросом.'}
        self.assertTrue(disproved_user_stated_fact(issue,request))

    def test_negative_instruction_does_not_authorize_discount(self):
        request='Не выдумывай скидки и цены.'
        issue={'type':'invented_fact','document':'calendar','quote':'Скидка 10%',
               'reason':'Фраза «Скидка 10%» противоречит ограничениям пользователя.'}
        self.assertFalse(disproved_user_stated_fact(issue,request))

    def test_unknown_school_benefit_stays_blocking(self):
        request='Школа проводит занятия для взрослых новичков. Методика и формат неизвестны.'
        issue={'type':'invented_fact','document':'calendar','quote':'На занятиях комфортно и эффективно.',
               'reason':'Фраза «комфортно и эффективно» не подтверждена запросом.'}
        self.assertFalse(disproved_user_stated_fact(issue,request))
        self.assertFalse(_nonblocking_editor_false_positive(issue,request))

    def test_generic_reader_advice_is_not_a_business_fact(self):
        issue={'type':'invented_fact','document':'calendar','quote':'Не игнорируйте первые признаки износа.',
               'reason':'Фраза «Не игнорируйте первые признаки износа» является неподтверждённым советом.'}
        self.assertTrue(_nonblocking_editor_false_positive(issue,'Напишите пост о профилактике.'))

    def test_staged_scene_with_explicit_confirmation_note_is_nonfactual(self):
        quote='Постановочная иллюстративная сцена для съёмки; детали необходимо подтвердить до публикации.'
        issue={'type':'invented_fact','document':'calendar','quote':quote,
               'reason':'Постановочная сцена не подтверждает, что это реальное занятие.'}
        self.assertTrue(_nonblocking_editor_false_positive(issue,'Студия проводит занятия по лепке.'))

    def test_unsupported_business_promise_is_not_suppressed(self):
        issue={'type':'invented_fact','document':'calendar','quote':'У каждого ученика быстрый результат.',
               'reason':'Обещание «быстрый результат» не подтверждено запросом.'}
        self.assertFalse(_nonblocking_editor_false_positive(issue,'Школа английского для взрослых.'))

    def test_explanation_may_quote_user_constraint_when_claim_is_grounded(self):
        parsed=parse_verdict('{"pass":false,"issues":[{"document":"calendar","type":"invented_fact",'
            '"evidence_id":"calendar:0001","claim":"Текст о занятиях.",'
            '"reason":"Это нарушает требование «не выдумывать комфорт»."}]}',
            strategy='Стратегия.',calendar='Текст о занятиях.',require_claim=True)
        self.assertFalse(parsed['pass'])

    def test_fabricated_issue_claim_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'issue.claim отсутствует'):
            parse_verdict('{"pass":false,"issues":[{"document":"calendar","type":"invented_fact",'
                '"evidence_id":"calendar:0001","claim":"уютные занятия",'
                '"reason":"Не подтверждено. Не выдумывать такие факты."}]}',
                strategy='Стратегия.',calendar='Текст о занятиях.',require_claim=True)

if __name__=='__main__': unittest.main()
