"""Receiving-boundary defects: invented counts, stale audits and changed artifacts."""
import copy,random,unittest
from completion_gate import can_close_task,bundle_review_matches,calendar_matches_parameters
from artifact_review import review_source_signature
from test_r93_bound_completion import delivery,parameters
from r94_bound_fixture import bind_report,calendar,REQUEST,STRATEGY

class ArtifactBindingTests(unittest.TestCase):
    def setUp(self):
        self.request=REQUEST;self.parameters=parameters(2,2)
        self.artifacts={'strategy':STRATEGY,'calendar':calendar(2,2)}
        self.report=bind_report(delivery(2,2),self.request,self.artifacts)
    def close(self):
        return can_close_task({'strategy','calendar'},self.report,expected_parameters=self.parameters,
            request=self.request,artifacts=self.artifacts)
    def test_exact_current_source_can_close(self):self.assertTrue(self.close())
    def test_report_cannot_replace_actual_artifacts(self):
        self.assertFalse(can_close_task({'strategy','calendar'},self.report,expected_parameters=self.parameters))
    def test_one_row_cannot_be_certified_as_sixteen(self):
        self.parameters=parameters(8,2);self.report=delivery(8,2);self.artifacts['calendar']=calendar()
        bind_report(self.report,self.request,self.artifacts)
        self.assertFalse(self.close())
    def test_same_total_in_wrong_weeks_or_cadence_rejected(self):
        self.artifacts['calendar']=calendar(4,1);bind_report(self.report,self.request,self.artifacts)
        self.assertFalse(self.close())
    def test_missing_duplicate_out_of_range_and_malformed_rows_rejected(self):
        original=self.artifacts['calendar'];lines=original.splitlines();rows=[line for line in lines if '1/Пн' in line]
        for body in (original.replace('2/Вт','1/Пн'),original.replace('2/Вт','3/Вт'),
                     original+'\n'+rows[0],original+'\n| malformed | row |',
                     original.replace('2/Вт','not-a-slot'),original.replace('2/Вт','02/Вт').replace('1/Вт','01/Пн')):
            self.assertFalse(calendar_matches_parameters(body,self.parameters),body)
    def test_empty_component_and_duplicate_column_rejected(self):
        body=self.artifacts['calendar']
        for bad in (body.replace('Count questions after publication',''),
                    body.replace('Необходимые материалы','Целевая аудитория')):
            self.assertFalse(calendar_matches_parameters(bad,self.parameters))
    def test_arbitrary_days_languages_and_escaped_pipes_allowed(self):
        body=self.artifacts['calendar'].replace('Пн','Alpha').replace('Вт','Beta')
        body=body.replace('A full explanation','Example \\| extended explanation')
        self.assertTrue(calendar_matches_parameters(body,self.parameters))
    def test_body_and_heading_changes_invalidate_both_audits(self):
        for kind in self.artifacts:
            original=self.artifacts[kind]
            for changed in (original+'\n\nNew text.', '# Another heading\n'+original, original.replace('explanation','new explanation')):
                if changed==original:continue
                self.artifacts[kind]=changed;self.assertFalse(self.close(),kind)
            self.artifacts[kind]=original
    def test_same_artifacts_for_different_request_are_not_approved(self):
        self.request+=' Changed requirements.';self.assertFalse(self.close())
    def test_missing_or_wrong_source_digest_rejected(self):
        for target in ('primary','reader'):
            for value in (None,'0'*64):
                report=copy.deepcopy(self.report)
                proof=report['independent_editor']['substance_review'] if target=='primary' else report['independent_editor']['independent_reader']['substance_review']
                proof['source_signature']=value
                self.assertFalse(bundle_review_matches(report['independent_editor'],self.request,self.artifacts))
    def test_current_digest_cannot_relabel_stale_substantive_contract(self):
        self.artifacts['calendar']=self.artifacts['calendar'].replace('concrete example','a different detailed example')
        for proof in (self.report['independent_editor']['substance_review'],self.report['independent_editor']['independent_reader']['substance_review']):
            proof['source_signature']=review_source_signature(self.request,self.artifacts)
        self.assertFalse(self.close())
    def test_primary_and_reader_contracts_are_different_and_not_interchangeable(self):
        editor=self.report['independent_editor']
        self.assertNotEqual(editor['substance_review']['signature'],editor['independent_reader']['substance_review']['signature'])
        editor['independent_reader']['substance_review']=copy.deepcopy(editor['substance_review']);self.assertFalse(self.close())
    def test_forged_assessment_quote_reference_and_criterion_rejected(self):
        for target in ('primary','reader'):
            for field,value in (('quote','Foreign content'),('evidence_id','foreign:0001'),('criterion','foreign'),
                                ('unit_id','foreign'),('reason','short'),('evidence_ids',[]),('instruction','Changed criterion')):
                report=copy.deepcopy(self.report);editor=report['independent_editor']
                proof=editor['substance_review'] if target=='primary' else editor['independent_reader']['substance_review']
                next(iter(proof['assessments'].values()))[field]=value
                self.assertFalse(bundle_review_matches(editor,self.request,self.artifacts),(target,field))
    def test_omitted_required_work_cannot_be_renamed_complete(self):
        editor=self.report['independent_editor'];proof=editor['substance_review'];key=proof['expected_checks'].pop()
        proof['assessments'].pop(key);self.assertFalse(self.close())
    def test_randomized_scopes_match_actual_rows(self):
        rng=random.Random(9401)
        for _ in range(30):
            w,c=rng.randint(1,26),rng.randint(1,7);body=calendar(w,c)
            self.assertTrue(calendar_matches_parameters(body,parameters(w,c)))
            self.assertFalse(calendar_matches_parameters(body,parameters(w,1 if c!=1 else 2)))
    def test_bad_artifact_shapes_fail_closed(self):
        for bad in (None,{}, {'strategy':STRATEGY}, {'strategy':STRATEGY,'calendar':False},
                    {'strategy':STRATEGY,'calendar':self.artifacts['calendar'],'other':'unapproved'}):
            self.assertFalse(bundle_review_matches(self.report['independent_editor'],self.request,bad))
    def test_counter_only_control_proof_cannot_pass_as_executed(self):
        for value in ({'passed':True},{'passed':True,'count':True},{'passed':True,'checked_calls':0}):
            report=copy.deepcopy(self.report);report['independent_editor']['substance_review']['controls']=value
            self.assertFalse(bundle_review_matches(report['independent_editor'],self.request,self.artifacts))
    def test_outer_whitespace_normalization_matches_storage(self):
        self.artifacts={key:'\n '+body+' \n' for key,body in self.artifacts.items()}
        self.assertTrue(self.close())

if __name__=='__main__':unittest.main()
