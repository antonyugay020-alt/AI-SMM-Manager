import unittest
from content_quality_engine import critical_issues

class Stage63Tests(unittest.TestCase):
    def test_invented_hours(self):
        self.assertTrue(critical_issues({'caption':'В нашем магазине режим работы: Пн-Пт 9:00-19:00.'}))
    def test_invented_app(self):
        self.assertTrue(critical_issues({'caption':'Используйте приложение для связи с аптекой.'}))
    def test_filler(self):
        self.assertTrue(critical_issues({'caption':'Заботимся о вашем здоровье и комфорте. Мы всегда рядом.'}))
    def test_topic_mismatch(self):
        self.assertTrue(critical_issues({'topic':'Что спросить о правилах возврата товара','caption':'Проверьте срок годности, упаковку и условия хранения.'}))
    def test_instruction_safe(self):
        self.assertFalse(critical_issues({'caption':'Проверяйте условия хранения по инструкции конкретного препарата.'}))

if __name__=='__main__': unittest.main()
