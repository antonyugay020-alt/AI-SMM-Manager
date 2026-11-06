import unittest
from content_quality_engine import inspect_post, critical_issues
from stage60_quality import quality_warnings

class QualityTests(unittest.TestCase):
    def test_medical_guarantee(self):
        self.assertTrue(critical_issues({'caption':'Это гарантирует безопасность и эффективность лечения.'}))
    def test_temperature(self):
        self.assertTrue(critical_issues({'caption':'Убедитесь, что температура не превышает 25°C.'}))
    def test_unverified_disposal(self):
        self.assertTrue(critical_issues({'caption':'Сдавайте просроченные лекарства в аптечный пункт.'}))
    def test_no_false_alarm(self):
        self.assertFalse(critical_issues({'caption':'Условия хранения зависят от инструкции конкретного препарата.'}))
    def test_short_natural_caption_is_not_a_quality_failure_by_length(self):
        self.assertFalse(critical_issues({'caption':'Сохраните этот совет.'}))
        self.assertEqual(inspect_post({'caption':'Сохраните этот совет.'}), [])

if __name__=='__main__': unittest.main()
