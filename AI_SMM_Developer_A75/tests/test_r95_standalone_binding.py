"""All standalone artifacts must match the original task and complete audit."""
import copy,unittest
from completion_gate import standalone_review_matches
from artifact_review import review_source_signature
from r95_text_fixture import text_review

class StandaloneBindingTests(unittest.TestCase):
    def setUp(self):
        self.request='Explain the requested comparison.';self.body='A complete comparison of two examples.'
        self.review=text_review(self.request,self.body)
    def accepted(self):return standalone_review_matches(self.review,self.request,self.body)
    def test_current_complete_evidence_accepted(self):self.assertTrue(self.accepted())
    def test_pass_label_alone_is_not_evidence(self):
        self.assertFalse(standalone_review_matches({'status':'PASS','pass':True},self.request,self.body))
    def test_changed_body_rejected(self):self.body+=' New assertion.';self.assertFalse(self.accepted())
    def test_changed_request_rejected(self):self.request+=' New requirement.';self.assertFalse(self.accepted())
    def test_relabelled_digest_does_not_replace_substantive_contract(self):
        self.body+=' New assertion.';self.review['source_signature']=review_source_signature(self.request,{'standalone':self.body})
        self.assertFalse(self.accepted())
    def test_every_required_dimension_is_mandatory(self):
        original=copy.deepcopy(self.review)
        for key in original['checks']:
            self.review=copy.deepcopy(original);self.review['checks'].pop(key);self.assertFalse(self.accepted())
    def test_non_boolean_success_and_omitted_coverage_rejected(self):
        for field,value in (('checks',dict.fromkeys(self.review['checks'],1)),('completed_dimensions',[]),
                            ('completed_dimensions',list(self.review['checks'])+['requirements']),('review_state','partial')):
            r=copy.deepcopy(self.review);r[field]=value;self.assertFalse(standalone_review_matches(r,self.request,self.body))
    def test_errors_or_findings_block_a_pass_label(self):
        for field,value in (('review_errors',{'requirements':'timeout'}),('issues',[{'reason':'Actual unresolved defect'}]),('pass',False)):
            r=copy.deepcopy(self.review);r[field]=value;self.assertFalse(standalone_review_matches(r,self.request,self.body))
    def test_same_family_author_and_reviewer_rejected(self):
        self.review['author_model']='fixture_editor:8b';self.assertFalse(self.accepted())
    def test_source_and_editor_must_be_separate(self):
        self.review['reviewer_models']['factual_grounding']='fixture_editor';self.assertFalse(self.accepted())
    def test_missing_author_or_independence_rejected(self):
        for key,value in (('author_model',None),('independent_model',False),('reviewer_models',{})):
            r=copy.deepcopy(self.review);r[key]=value;self.assertFalse(standalone_review_matches(r,self.request,self.body))
    def test_missing_or_stale_digest_rejected(self):
        for value in (None,'0'*64):
            self.review['source_signature']=value;self.assertFalse(self.accepted())
    def test_missing_proof_or_invented_quote_rejected(self):
        r=copy.deepcopy(self.review);r.pop('substance_review');self.assertFalse(standalone_review_matches(r,self.request,self.body))
        next(iter(self.review['substance_review']['assessments'].values()))['quote']='Foreign text';self.assertFalse(self.accepted())
    def test_unknown_or_omitted_criterion_rejected(self):
        proof=self.review['substance_review'];key=proof['expected_checks'].pop();proof['assessments'].pop(key);self.assertFalse(self.accepted())
    def test_fresh_review_allows_corrected_text(self):
        self.body+=' An additional checked explanation.';self.review=text_review(self.request,self.body);self.assertTrue(self.accepted())
    def test_outer_whitespace_matches_storage(self):self.body='\n '+self.body+' \n';self.assertTrue(self.accepted())
    def test_empty_and_non_text_inputs_fail_closed(self):
        for bad in ('',None,{},False):
            self.assertFalse(standalone_review_matches(self.review,self.request,bad))
            self.assertFalse(standalone_review_matches(self.review,bad,self.body))

if __name__=='__main__':unittest.main()
