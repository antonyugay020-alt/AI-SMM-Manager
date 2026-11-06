"""A Windows GUI verdict must certify requested volume, not document names."""
import unittest
import hashlib
from live_gui_workflow_acceptance import verified_delivery_in_gui_report


REQUEST='Составь стратегию и календарь на 2 недели, по 2 публикации в неделю, всего 4 публикации.'


def report(checked=4, total=4, unfulfilled=None):
    return {'delivery_reports': [{'request_sha256':hashlib.sha256(REQUEST.encode('utf-8')).hexdigest(),
                                  'task_signature':'c'*64,
                                  'scope':{'weeks':2,'per_week':2,'planned_total':total},
                                  'checked_posts':checked,
                                  'unfulfilled':[] if unfulfilled is None else unfulfilled}]}


class WindowsDeliveryVolumeGateTests(unittest.TestCase):
    def test_correct_complete_delivery_passes(self):
        self.assertTrue(verified_delivery_in_gui_report(report(),REQUEST))

    def test_three_of_four_fails_even_if_documents_exist(self):
        self.assertFalse(verified_delivery_in_gui_report(report(3),REQUEST))

    def test_wrong_total_fails(self):
        self.assertFalse(verified_delivery_in_gui_report(report(total=3),REQUEST))

    def test_unresolved_editorial_issues_fail(self):
        self.assertFalse(verified_delivery_in_gui_report(report(unfulfilled=['one missing field']),REQUEST))

    def test_same_count_but_different_task_rejected(self):
        self.assertFalse(verified_delivery_in_gui_report(report(), REQUEST+' Ещё одно условие.'))

    def test_missing_task_signature_rejected(self):
        data=report(); del data['delivery_reports'][0]['task_signature']
        self.assertFalse(verified_delivery_in_gui_report(data,REQUEST))

    def test_missing_report_fails(self):
        self.assertFalse(verified_delivery_in_gui_report({'delivery_reports':[]},REQUEST))

    def test_boolean_is_not_valid_integer_count(self):
        self.assertFalse(verified_delivery_in_gui_report(report(True),REQUEST))

    def test_new_random_briefs_have_distinct_expected_scope(self):
        from random_acceptance_tasks import generate_acceptance_tasks
        from content_requirements import publication_scope
        for seed in (123456,732419,1079331):
            cases=generate_acceptance_tasks(seed,5)
            self.assertEqual(len({item['scenario_id'] for item in cases}),5)
            for item in cases:
                scope=publication_scope(item['request'])
                self.assertEqual(scope.total,item['posts'])
                self.assertFalse(verified_delivery_in_gui_report(report(checked=0,total=0),item['request']))

if __name__=='__main__':unittest.main()
