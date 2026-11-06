import unittest
from factual_claim_audit import scan_unsupported_business_claims
from independent_acceptance_editor import parse_verdict

class ClaimProvenanceA13(unittest.TestCase):
    def test_a12_language_school_benefits_are_blocked(self):
        request=('Проект: языковой клуб, разговорные занятия для взрослых новичков в небольших группах. '
                 'Аудитория хочет увереннее говорить. Не выдумывай гарантированный результат.')
        strategy=('Занятия проходят комфортно и эффективно. Наша цель — помочь ученикам говорить увереннее.')
        calendar=('В небольших группах вы получаете обратную связь и чувствуете себя увереннее.')
        findings=scan_unsupported_business_claims(request,strategy,calendar)
        joined=' '.join(i['quote'].casefold() for i in findings)
        self.assertIn('комфортно',joined)
        self.assertIn('обратную связь',joined)
        self.assertIn('увереннее',joined)
        self.assertFalse(any('наша цель' in i['quote'].casefold() for i in findings))

    def test_user_ban_does_not_authorize_safety_promise(self):
        request='Мастерская делает диагностику. Нельзя обещать полную безопасность.'
        docs='Регулярная диагностика — залог безопасности.'
        findings=scan_unsupported_business_claims(request,docs,'')
        self.assertTrue(any('безопасности' in i['quote'].casefold() for i in findings))

    def test_explicitly_supported_service_benefit_is_accepted(self):
        request='Уроки проходят в комфортной обстановке, обратную связь даёт преподаватель.'
        docs='На уроках комфортная обстановка и преподаватель даёт обратную связь.'
        self.assertEqual(scan_unsupported_business_claims(request,docs,''),[])

    def test_unknown_ceramics_process_and_staff_are_blocked(self):
        request='Студия проводит занятия по ручной лепке для взрослых новичков. Процесс не описан.'
        docs=('Занятие состоит из четырёх этапов. Сотрудник показывает, как подготовить глину.')
        findings=scan_unsupported_business_claims(request,'',docs)
        self.assertGreaterEqual(len(findings),2)

    def test_neutral_reader_advice_is_not_business_claim(self):
        request='Подготовь образовательную публикацию о проверке велосипедных тормозов.'
        docs=('Не игнорируйте первые признаки износа и проверьте тормоза перед поездкой. '
              'Изношенные колодки снижают эффективность торможения.')
        self.assertEqual(scan_unsupported_business_claims(request,'',docs),[])

    def test_staged_visual_cell_does_not_mask_unknown_business_caption(self):
        request='Для студии ручной лепки. Не выдумывай этапы занятия.'
        row='| Визуал: постановочный кадр с глиной | Текст: Сотрудник показывает этапы подготовки глины. |'
        findings=scan_unsupported_business_claims(request,'',row)
        self.assertTrue(any('Сотрудник показывает' in x['quote'] for x in findings))

    def test_editor_claim_is_independently_grounded_even_if_reason_quotes_brief(self):
        source='Языковой клуб помогает ученикам чувствовать себя комфортно.'
        raw=('{"pass":false,"issues":[{"document":"calendar","type":"invented_fact",'
             '"evidence_id":"calendar:0001","claim":"ученикам чувствовать себя комфортно",'
             '"reason":"Это не подтверждено: пользователь запретил «выдумывать комфорт»."}]}')
        verdict=parse_verdict(raw,strategy='Стратегия.',calendar=source,require_claim=True)
        self.assertFalse(verdict['pass'])
        self.assertIn('комфортно',verdict['issues'][0]['quote'])

    def test_unique_exact_claim_recovers_the_host_evidence_segment(self):
        raw=('{"pass":false,"issues":[{"document":"calendar","type":"invented_fact",'
             '"evidence_id":"calendar:0001","claim":"обратную связь",'
             '"reason":"Утверждение не подтверждено сведениями о проекте."}]}')
        verdict=parse_verdict(raw,strategy='Стратегия.',
                              calendar='Один сегмент.\nВ другом есть обратную связь.',
                              require_claim=True)
        self.assertEqual('calendar:0002',verdict['issues'][0]['evidence_id'])
        self.assertIn('обратную связь',verdict['issues'][0]['evidence_segment'])

if __name__=='__main__': unittest.main()
