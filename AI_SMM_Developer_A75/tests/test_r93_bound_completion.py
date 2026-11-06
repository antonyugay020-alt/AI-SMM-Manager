"""Authoritative scope, evidence completeness and non-coercing counters."""
import copy,random,unittest
from completion_gate import can_close_task,delivery_matches_parameters
import test_architecture_gate_expansion as previous
from r94_bound_fixture import bind_report,calendar,REQUEST,STRATEGY


def proof():
    return {'policy':'substantive_delivery_r82','complete':True,'pass':True,
            'expected_checks':['content','scope'],'assessments':{'content':{'status':'satisfied'},'scope':{'status':'satisfied'}},'controls':{'passed':True}}

def delivery(weeks=1,cadence=1):
    value=previous.PersistenceCompletionBoundaryTests().sample();total=weeks*cadence
    value.update(scope={'weeks':weeks,'per_week':cadence,'planned_total':total},checked_posts=total,
                 content_components_checked={key:total for key in value['content_components_checked']})
    value['independent_editor'].update(substance_review=proof(),independent_reader={
        'status':'PASS','model':'independent-reader','independent_from':'primary-reviewer',
        'independence_verified':True,'substance_review':proof()})
    return value

def parameters(weeks=1,cadence=1,media=False):
    return {'kind':'bundle','schedule':{'weeks':weeks,'per_week':cadence,'total':weeks*cadence},'requested_media':media}

class BoundCompletionTests(unittest.TestCase):
    def close(self,value,expected=None):
        # Historical scope tests now also carry authentic contract-bound fixtures.
        p=expected or parameters();scope=value.get('scope') or {};w=scope.get('weeks');c=scope.get('per_week')
        artifacts={'strategy':STRATEGY,'calendar':calendar(w,c) if type(w) is int and 1<=w<=26 and type(c) is int and 1<=c<=7 else calendar()}
        # Only upgrade the old untouched happy fixture, never a negative proof.
        if value.get('independent_editor')==delivery().get('independent_editor'):bind_report(value,REQUEST,artifacts)
        return can_close_task({'strategy','calendar'},value,expected_parameters=p,request=REQUEST,artifacts=artifacts)
    def test_internally_consistent_wrong_volume_cannot_close_original_task(self):
        self.assertFalse(self.close(delivery(1,1),parameters(8,2)))
        self.assertFalse(delivery_matches_parameters(delivery(1,1),parameters(8,2)))
    def test_equal_totals_do_not_hide_wrong_cadence_or_duration(self):
        self.assertFalse(self.close(delivery(2,2),parameters(4,1)))
    def test_correct_scopes_across_randomized_bounds_remain_accepted(self):
        rng=random.Random(9301)
        for _ in range(50):
            weeks,cadence=rng.randint(1,26),rng.randint(1,7)
            self.assertTrue(self.close(delivery(weeks,cadence),parameters(weeks,cadence)))
    def test_boolean_float_string_and_missing_component_counts_are_not_integers(self):
        for key in delivery()['content_components_checked']:
            for bad in (True,1.0,'1',None):
                item=delivery();item['content_components_checked'][key]=bad
                self.assertFalse(self.close(item),(key,bad))
                self.assertFalse(can_close_task({'strategy','calendar'},item),(key,bad))
    def test_invalid_report_scope_and_invalid_host_parameters_fail_closed(self):
        for bad in ({},None,{'weeks':True,'per_week':1,'planned_total':1},{'weeks':27,'per_week':1,'planned_total':27}):
            item=delivery();item['scope']=bad;self.assertFalse(self.close(item))
        for bad in ({'kind':'unknown'}, {'kind':'bundle','schedule':None,'requested_media':False},parameters(media=True)):
            self.assertFalse(can_close_task({'strategy','calendar'},delivery(),expected_parameters=bad))
    def test_missing_substance_or_independent_reader_cannot_be_replaced_by_pass_label(self):
        for key in ('substance_review','independent_reader'):
            item=delivery();item['independent_editor'].pop(key);self.assertFalse(self.close(item))
    def test_reader_missing_assessments_is_not_complete_evidence(self):
        item=delivery();item['independent_editor']['independent_reader']['substance_review'].pop('assessments')
        self.assertFalse(self.close(item))
    def test_duplicate_unknown_policy_or_failed_controls_are_not_complete_evidence(self):
        for key,val in (('policy','unknown'),('expected_checks',['content','content']),('controls',{'passed':False}),('pass',False)):
            item=delivery();item['independent_editor']['substance_review'][key]=val;self.assertFalse(self.close(item))
    def test_same_voice_or_unverified_independence_cannot_close(self):
        for changes in ({'independence_verified':False},{'model':'primary-reviewer'},{'status':'BLOCKED'}):
            item=delivery();item['independent_editor']['independent_reader'].update(changes);self.assertFalse(self.close(item))
    def test_unavailable_or_partial_editor_cannot_claim_pass(self):
        for status in ('unavailable','partial','rejected'):
            item=delivery();item['independent_editor']['review_state']=status;self.assertFalse(self.close(item))
        item=delivery();item['independent_editor']['review_errors']={'read':'Not completed'};self.assertFalse(self.close(item))
    def test_expected_media_is_not_fulfilled_by_a_text_only_status(self):
        self.assertFalse(self.close(delivery(),parameters(media=True)))
    def test_explicit_backend_precheck_can_measure_artifacts_before_editor_runs(self):
        item=delivery();item.pop('independent_editor')
        self.assertTrue(can_close_task({'strategy','calendar'},item,require_editor=False,expected_parameters=parameters()))
        self.assertFalse(can_close_task({'strategy','calendar'},item,expected_parameters=parameters()))

if __name__=='__main__':unittest.main()
