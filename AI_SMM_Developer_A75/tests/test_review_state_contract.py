"""Fail-closed contract for model-supplied independent review states."""
import unittest
from reviewer_outcomes import review_unavailable

class ReviewStateContractTests(unittest.TestCase):
    def test_unknown_review_state_is_unavailable(self):
        for state in ('approved', 'PASS', 'pending', '', None, 1, False, ['complete']):
            with self.subTest(state=state):
                self.assertTrue(review_unavailable({'pass': True, 'issues': [], 'review_state': state}))

    def test_complete_and_legacy_verified_structures(self):
        self.assertFalse(review_unavailable({'pass': True, 'issues': [], 'review_state': 'complete'}))
        self.assertFalse(review_unavailable({'pass': True, 'issues': []}))

    def test_final_gate_cannot_approve_unknown_review_state(self):
        from independent_quality_gate import final_quality_gate
        from test_windows_acceptance_v4 import FinalEditorialGateTests
        artifact=FinalEditorialGateTests.prepared()
        calls=[]
        def bogus(*args):
            calls.append(1)
            return {'pass':True, 'issues':[], 'review_state':'approved'}
        result=final_quality_gate(artifact,'Original request',bogus)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'BLOCKED')
        self.assertEqual(result['content_delivery']['independent_editor']['review_state'],'unavailable')

    def test_unknown_state_never_overrides_valid_structural_failure(self):
        self.assertTrue(review_unavailable({'pass': 'true', 'issues': [], 'review_state': 'complete'}))
        self.assertTrue(review_unavailable({'pass': True, 'issues': None, 'review_state': 'complete'}))

if __name__ == '__main__': unittest.main()
