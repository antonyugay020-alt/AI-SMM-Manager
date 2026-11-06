import unittest
from semantic_editor import audit_post_with_independent_model
from review_arbitration import FindingReviewUnavailable

class NewFindingAdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.post={'caption':'New unverified shop promise'}
        self.first={'field':'caption','quote':'New','reason':'Initial objection'}
        self.second={'field':'caption','quote':'shop promise','reason':'A different factual problem'}
        self.calls=[]

    def audit(self,ask,model,request,facts,slot,post,*args,**kwargs):
        self.calls.append(('audit',model))
        return (None,[self.first]) if model=='reviewer-a:8b' else (None,[self.second])

    def judge(self,ask,model,request,fields,issues,**kwargs):
        self.calls.append(('judge',model))
        if model=='reviewer-b:8b':return [],[self.first]
        if model=='reviewer-c:8b':return [self.second],[]
        raise AssertionError(model)

    def run_case(self,models):
        return audit_post_with_independent_model(None,'writer:8b',models,'Task',{}, {},self.post,
                    audit_function=self.audit,adjudicate_function=self.judge)

    def test_confirmed_new_finding_is_returned_for_repair_not_unavailable(self):
        revised,issues=self.run_case(['reviewer-a:8b','reviewer-b:8b','reviewer-c:8b'])
        self.assertIsNone(revised)
        self.assertEqual(issues,[self.second])
        self.assertEqual(self.calls,[('audit','reviewer-a:8b'),('judge','reviewer-b:8b'),
            ('audit','reviewer-b:8b'),('judge','reviewer-c:8b')])

    def test_blank_evidence_cannot_authorize_repair(self):
        def bad_audit(*args,**kwargs):
            return None,[{'field':'caption','quote':'','reason':'Ungrounded'}]
        with self.assertRaises(FindingReviewUnavailable):
            audit_post_with_independent_model(None,'writer:8b',['reviewer-a:8b'],
                'Task',{}, {},self.post,audit_function=bad_audit,
                adjudicate_function=self.judge)
        self.assertEqual(self.calls,[])

    def test_only_two_families_fail_closed(self):
        with self.assertRaises(FindingReviewUnavailable) as caught:
            self.run_case(['reviewer-a:8b','reviewer-b:8b'])
        self.assertEqual(caught.exception.findings[0]['adjudication'],'unavailable')

    def test_third_dismissal_still_fails_closed(self):
        def dismiss(*args,**kwargs):
            if args[1]=='reviewer-b:8b':return [],[self.first]
            return [],[self.second]
        with self.assertRaises(FindingReviewUnavailable):
            audit_post_with_independent_model(None,'writer:8b',
                ['reviewer-a:8b','reviewer-b:8b','reviewer-c:8b'],'Task',{}, {},self.post,
                audit_function=self.audit,adjudicate_function=dismiss)

if __name__=='__main__':unittest.main()
