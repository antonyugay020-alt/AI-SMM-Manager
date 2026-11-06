import unittest
from tempfile import TemporaryDirectory

from document_recovery import DocumentDraft
from factual_claim_audit import scan_unsupported_business_claims
from strategy_recovery import produce_checked_strategy


class ConfirmedServiceGroundingTests(unittest.TestCase):
    def test_user_confirmed_workshop_services_are_not_rejected_as_inventions(self):
        request=('Мастерская «Веломир» делает диагностику, замену тормозных колодок '
                 'и настройку переключателей.')
        strategy=('В мастерской проводим диагностику, замену тормозных колодок '
                  'и настройку переключателей.')
        self.assertEqual(scan_unsupported_business_claims(request,strategy,''),[])

    def test_confirmed_service_does_not_authorize_unconfirmed_result(self):
        request='Мастерская делает диагностику велосипедов.'
        strategy=('Мы проводим диагностику велосипедов и гарантируем полную безопасность '
                  'каждой поездки.')
        findings=scan_unsupported_business_claims(request,strategy,'')
        self.assertTrue(any('безопасност' in x['quote'].casefold() for x in findings))


class SavedRejectedDraftRecoveryTests(unittest.TestCase):
    def test_no_progress_keeps_rejected_draft_durable_and_never_generates(self):
        with TemporaryDirectory() as root:
            store=DocumentDraft(root,'sig','strategy')
            rejected='Стратегия с неподтверждённым обещанием'
            store.save(rejected,False,'Уберите обещание')
            calls=[]
            def generate(*args):
                self.fail('A persisted rejected draft must remain in targeted recovery')
            def repair(body,reason,attempt):
                calls.append((body,reason,attempt))
                return rejected+'; изменённая формулировка всё ещё содержит обещание'
            content,valid,reason=produce_checked_strategy(
                'Сделайте стратегию',2,generate,
                lambda request,text:(False,'Осталось неподтверждённое обещание'),
                draft=store.load(),repair=repair,checkpoint=store.save,attempts=2)
            self.assertFalse(valid)
            self.assertEqual(calls[0][0],rejected)
            self.assertEqual(len(calls),2)
            checkpoint=store.load()
            self.assertFalse(checkpoint['valid'])
            self.assertEqual(checkpoint['content'],content)
            self.assertTrue(any(item['content']==rejected for item in checkpoint['history']))
            self.assertIn('Осталось неподтверждённое обещание',reason)


if __name__=='__main__':
    unittest.main()
