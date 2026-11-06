import unittest
from reviewer_outcomes import review_unavailable

class ReviewContractTests(unittest.TestCase):
    def test_contradictory_approval(self):
        self.assertTrue(review_unavailable({'review_state':'complete','pass':True,'issues':[{'reason':'error'}]}))
    def test_incomplete_substance(self):
        self.assertTrue(review_unavailable({'review_state':'complete','pass':True,'issues':[],'substance_review':{'pass':True,'complete':False}}))
    def test_normal_completed_approval(self):
        self.assertFalse(review_unavailable({'review_state':'complete','pass':True,'issues':[]}))
    def test_null_state(self):
        self.assertTrue(review_unavailable({'review_state':None,'pass':True,'issues':[]}))
