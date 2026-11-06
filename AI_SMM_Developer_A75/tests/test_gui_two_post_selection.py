import unittest
from live_gui_workflow_acceptance import select_two_publication_brief
from content_requirements import publication_scope

class TestGuiTwoPostSelection(unittest.TestCase):
    def test_random_seeds_always_select_two_posts(self):
        for seed in range(45):
            _,task=select_two_publication_brief(seed)
            self.assertEqual(task['posts'],2)
            self.assertEqual(publication_scope(task['request']).total,2)
    def test_selection_is_reproducible_and_varied(self):
        observed=[select_two_publication_brief(seed) for seed in range(50,80)]
        self.assertEqual(observed,[select_two_publication_brief(seed) for seed in range(50,80)])
        self.assertGreaterEqual(len({t['category'] for _,t in observed}),4)
        self.assertGreaterEqual(len({t['request'] for _,t in observed}),8)
