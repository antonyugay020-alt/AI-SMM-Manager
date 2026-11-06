import unittest

from publication_writer import repeated_rejected_claims, _repair_claim_constraints


class RejectedClaimRepairGuardTests(unittest.TestCase):
    def test_exact_rejected_claim_cannot_survive_targeted_repair(self):
        post={'caption':'Напишите нам — мы ответим на ваши вопросы.',
              'visual':'Будущий кадр для публикации.', 'cta':'Задайте вопрос.'}
        issues=[{'quote':'мы ответим на ваши вопросы',
                 'reason':'Не подтверждён процесс ответа.'}]
        errors=repeated_rejected_claims(post,issues)
        self.assertEqual(len(errors),1)
        self.assertIn('мы ответим на ваши вопросы',errors[0])

    def test_reworded_or_removed_claim_is_left_to_independent_review(self):
        post={'caption':'Уточните условия у владельца проекта.',
              'visual':'Подготовить нейтральную иллюстрацию.', 'cta':'Задайте вопрос.'}
        issues=[{'quote':'мы ответим на ваши вопросы',
                 'reason':'Не подтверждён процесс ответа.'}]
        self.assertEqual(repeated_rejected_claims(post,issues),[])

    def test_short_common_fragments_do_not_block_unrelated_copy(self):
        post={'caption':'Условия можно уточнить у владельца.', 'cta':'Напишите.'}
        self.assertEqual(repeated_rejected_claims(post,[{'quote':'мы'}]),[])

    def test_same_class_response_promise_requires_independent_review(self):
        post={'caption':'Узнайте о тормозах и их обслуживании.',
              'cta':'Напишите нам — мы расскажем, как проверить тормоза дома.'}
        issue={'quote':'мы ответим на вопросы', 'statement_class':'business_fact',
               'field_role':'CTA', 'reason':'Не подтверждено обещание ответа компании.'}
        errors=repeated_rejected_claims(post,[issue])
        self.assertEqual(errors,[])

    def test_repair_prompt_receives_class_constraint(self):
        issue={'quote':'мы ответим на вопросы', 'statement_class':'business_fact',
               'field_role':'CTA', 'reason':'Не подтверждено обещание ответа компании.', 'adjudication':'unsupported'}
        constraints=_repair_claim_constraints([issue])
        self.assertEqual(len(constraints),1)
        self.assertIn('по смыслу',constraints[0])

    def test_service_outcome_paraphrase_requires_independent_review(self):
        post={'caption':'Настройка переключателей делает поездку плавнее и комфортнее.'}
        issue={'quote':'Настроенные переключатели делают езду комфортнее.',
               'statement_class':'service_outcome',
               'reason':'Не подтвержден результат услуги.'}
        errors=repeated_rejected_claims(post,[issue])
        self.assertEqual(errors,[])


if __name__ == '__main__':
    unittest.main()
