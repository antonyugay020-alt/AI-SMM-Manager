import unittest
from publication_writer import validate_single
from task_routing import resolve_request

class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.slot={'week':2,'day':'Ср','topic':'Как выбрать материал для столешницы','format':'Reels'}
        self.post={'id':'2/Ср','topic':self.slot['topic'],'visual':'Кадр 1: сравнение материалов. Кадр 2: таблица свойств. Кадр 3: вывод с пояснениями.','caption':'Сравните износостойкость, условия ухода и ограничения разных материалов. '*6,'cta':'Сохраните список','kpi':'Сохранения','audience':'Владельцы жилья','measurement':'Статистика Instagram','materials':'Схемы'}
    def test_mismatched_id_rejected(self):
        self.post['id']='1/Ср'
        self.assertIn('публикация принадлежит другому слоту',validate_single(self.post,self.slot))
    def test_mismatched_topic_rejected(self):
        self.post['topic']='Как ухаживать за автомобилем'
        self.assertIn('тема публикации не соответствует утверждённому плану',validate_single(self.post,self.slot))
    def test_valid_slot_not_rejected_for_identity(self):
        issues=validate_single(self.post,self.slot)
        self.assertNotIn('публикация принадлежит другому слоту',issues)
        self.assertNotIn('тема публикации не соответствует утверждённому плану',issues)
    def test_new_business_is_not_clarification(self):
        self.assertEqual(resolve_request('Новый бизнес: пекарня','Старое задание','Уточните платформу?')[1],'new')
if __name__=='__main__': unittest.main()
