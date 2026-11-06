import unittest
from artifact_review import SubstanceReviewContract, ReviewContractError

class ReviewerFeedbackBindingTest(unittest.TestCase):
    def test_defect_without_linked_issue_fails_with_criterion_id(self):
        contract=SubstanceReviewContract(
            'sig', {'strategy:0001':'An original statement requiring assessment.'},
            {'Q_abc':{'unit_id':'u','criterion':'clarity','evidence_ids':['strategy:0001'],
                      'instruction':'Review clarity'}},
            {'C_x':{'task':'t','source_points':['p'],'candidate':'c','expected':True}})
        result={'Q_abc':{'status':'defect','evidence_id':'strategy:0001',
                         'reason':'This specific statement is insufficient.'}}
        with self.assertRaisesRegex(ReviewContractError,'Q_abc'):
            contract.validate(result, [], {'C_x':True})
    def test_defect_with_bound_issue_is_not_accepted(self):
        contract=SubstanceReviewContract(
            'sig', {'strategy:0001':'An original statement requiring assessment.'},
            {'Q_abc':{'unit_id':'u','criterion':'clarity','evidence_ids':['strategy:0001'],
                      'instruction':'Review clarity'}},
            {'C_x':{'task':'t','source_points':['p'],'candidate':'c','expected':True}})
        result={'Q_abc':{'status':'defect','evidence_id':'strategy:0001',
                         'reason':'This specific statement is insufficient.'}}
        issues=[{'criterion_id':'Q_abc','evidence_id':'strategy:0001','type':'quality'}]
        proof=contract.validate(result,issues,{'C_x':True})
        self.assertFalse(proof['pass'])
        self.assertEqual(proof['assessments']['Q_abc']['status'],'defect')

if __name__=='__main__':unittest.main()
