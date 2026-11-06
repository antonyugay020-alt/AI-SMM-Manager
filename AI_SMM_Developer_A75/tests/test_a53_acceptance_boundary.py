import unittest
from unittest.mock import patch

import production_pipeline
from independent_acceptance_editor import ValidatedEditorialVerdict
from strategy_recovery import produce_checked_strategy


class AcceptanceBoundaryTests(unittest.TestCase):
    def test_full_source_inventory_is_only_requested_at_delivery_boundary(self):
        seen = {}
        reviews = []
        result = {
            'action':'compound_documents',
            'documents':[{'kind':'strategy','content':'Стратегия','valid':True},
                         {'kind':'calendar','content':'Календарь','valid':True}],
            'content_delivery':{
                'task_orchestration':{'completed':True},
                'deliverables':{'strategy':'validated','calendar':'validated'},
                'unfulfilled':[], 'rendered_media':'not_requested',
                'scope':{'weeks':1,'per_week':1,'planned_total':1},
                'checked_posts':1,
                'content_components_checked':{field:1 for field in
                    ('caption','visual','cta','kpi','audience','measurement','materials')},
                'plan_preflight':{'reviewer_available':True,'unresolved_slots':[]},
            },
        }

        def execute(request, context, progress=None):
            seen.update(context)
            return result

        def reviewer(request, strategy, calendar):
            reviews.append((request,strategy,calendar))
            self.assertEqual(strategy, 'Стратегия')
            self.assertEqual(calendar, 'Календарь')
            return ValidatedEditorialVerdict({'pass':True,'issues':[],
                'disproved_absence_claims':[],'review_state':'complete'})

        with patch.object(production_pipeline, 'run_compound_with_retry',
                          side_effect=lambda executor,request,state,progress,max_attempts,model:
                              executor(request,state,progress)):
            production_pipeline.run_document_task('Проектный запрос', {}, execute=execute,
                                                  final_reviewer=reviewer)

        self.assertNotIn('publication_evidence_reviewer', seen)
        self.assertNotIn('production_acceptance_boundary', seen)
        self.assertEqual(reviews, [('Проектный запрос','Стратегия','Календарь')])

    def test_saved_strategy_repair_stops_when_same_gate_finding_remains(self):
        repairs=[]
        events=[]
        result=produce_checked_strategy(
            'Исходный запрос', 2,
            generate=lambda *_: self.fail('saved draft must not be replaced'),
            validate=lambda _request, _strategy: (False, 'Не подтверждённое утверждение: факт отсутствует'),
            draft={'content':'Сохранённый черновик','reason':'Не подтверждённое утверждение: факт отсутствует'},
            repair=lambda text, reason, attempt: repairs.append(attempt) or text,
            report=lambda stage, **data: events.append((stage,data)),
        )
        self.assertFalse(result[1])
        self.assertEqual(repairs, [1])
        self.assertIn('strategy_recovery_stopped_no_progress', [stage for stage,_ in events])


if __name__ == '__main__':
    unittest.main()
