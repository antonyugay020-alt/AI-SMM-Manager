import unittest
from task_provenance import TaskProvenance

class ProvenanceTests(unittest.TestCase):
    def test_round_trip(self):
        record=TaskProvenance.from_request('План на одну неделю')
        self.assertEqual(TaskProvenance.restore(record.as_dict()),record)
    def test_changed_request_blocked(self):
        record=TaskProvenance.from_request('План на одну неделю')
        with self.assertRaises(ValueError): record.verify('План на две недели')
    def test_tamper_blocked(self):
        record=TaskProvenance.from_request('16 публикаций').as_dict()
        record['original_request']='15 публикаций'
        with self.assertRaises(ValueError): TaskProvenance.restore(record)
    def test_empty_blocked(self):
        with self.assertRaises(ValueError): TaskProvenance.from_request('  ')
