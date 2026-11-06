import unittest
from task_context import extract_facts, missing_essentials

class TaskContextFixTests(unittest.TestCase):
    def test_flower_brief(self):
        prompt = '''Разработай профессиональную SMM-стратегию для небольшого цветочного магазина.
1. Исходные данные
Бизнес: небольшой розничный цветочный магазин.
Город: Усть-Каменогорск, Казахстан.
Основная площадка: Instagram.
Целевая аудитория:
Мужчины 20–55 лет, покупающие цветы для близких.
Женщины 20–55 лет, приобретающие цветы для подарков.
Молодые пары.
Рекламный бюджет: 120 000 тенге на весь период.
Срок стратегии: 3 месяца — 13 недель.
Частота публикаций: строго 3 публикации в неделю, всего ровно 39 публикаций.
2. Важные ограничения'''
        f = extract_facts(prompt)
        self.assertEqual(f['business'], 'небольшой розничный цветочный магазин')
        self.assertIn('Мужчины 20–55', f['audience'])
        self.assertIn('Молодые пары', f['audience'])
        self.assertEqual(f['budget'], '120000 тенге')
        self.assertFalse(missing_essentials(f), f)
    def test_unrelated_business(self):
        f = extract_facts('Бизнес: ремонт велосипедов\\nЦелевая аудитория: владельцы велосипедов\\nПлощадка: Instagram\\nСрок: 4 недели'.replace('\\n','\n'))
        self.assertEqual(f['business'], 'ремонт велосипедов')
        self.assertEqual(f['audience'], 'владельцы велосипедов')
        self.assertFalse(missing_essentials(f), f)
    def test_no_invention(self):
        f = extract_facts('Разработай стратегию для моего проекта.')
        self.assertIn('business', missing_essentials(f))
        self.assertIn('audience', missing_essentials(f))
if __name__ == '__main__': unittest.main()
