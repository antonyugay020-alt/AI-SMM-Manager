"""Editorial speech acts, preserved first-pass scores and bounded real recovery."""
import io
import json
import unittest
from unittest.mock import patch

from acceptance_policy import attempt_limit, final_repair_limit, workflow_outcome_metrics
from assertion_scope import classify_obvious_non_business
from claim_support import adjudicate_factual_findings, public_copy_findings
from generic_text_review import run_quality_cycle, CHECKS
from production_pipeline import run_document_task
import test_windows_acceptance_v4 as gate_fixtures


class EditorialSpeechActTests(unittest.TestCase):
    def test_questions_are_not_business_facts_regardless_of_length_and_subject(self):
        for preface in ('Мы хотим узнать', 'Поэтому мы хотим узнать', 'Нам интересно узнать',
                        'Вопрос к читателям', 'We would like to know', 'A question for you'):
            question = ('what would you like to ask about your situation '+ 'in more detail '*15+'?'
                        if preface[0] in 'WA' else 'какие вопросы возникают у вас '+
                        'когда вы сравниваете разные варианты '*15+'?')
            quote = preface+': '+question
            scope = classify_obvious_non_business({'content': quote, 'field_type': 'caption'})
            self.assertIsNotNone(scope, quote)
            self.assertEqual(scope['statement_class'], 'audience_prompt')

    def test_question_suffix_does_not_hide_a_business_assertion_or_promise(self):
        for quote in ('Мы сортируем каждый заказ вручную: какие вопросы у вас?',
                      'Мы гарантируем результат: что вас интересует?',
                      'Мы хотим узнать: как мы доставляем каждый заказ за час?',
                      'Our team checks every order: what would you like to know?'):
            self.assertIsNone(classify_obvious_non_business({'content': quote, 'field_type': 'caption'}), quote)

    def test_query_and_neutral_cta_need_no_business_evidence_but_fulfilment_does(self):
        for text in ('Мы хотим узнать: что для вас важно при выборе?', 'Напишите нам.'):
            with patch('claim_support.request_chat') as model:
                audit = adjudicate_factual_findings('Подготовь пост.', public_copy_findings(text))
            self.assertEqual(audit['blocking'], [])
            self.assertTrue(audit['non_business'])
            model.assert_not_called()
        text = 'Поделитесь вопросом — мы поможем вам выбрать подходящий вариант.'
        def waived(*args):
            return {'statement_class': 'invitation_cta', 'verdict': 'supported',
                    'source_quote_id': 'none', 'reason': 'Это только приглашение к действию.'}
        result = adjudicate_factual_findings('Подготовь пост.', public_copy_findings(text), ask=waived)
        self.assertTrue(result['blocking'])

    def test_strategy_orientation_is_planning_but_public_claims_are_not(self):
        text = 'Ориентация на создание доверия и предоставление базовой информации.'
        scope = classify_obvious_non_business({'content': text, 'field_role': 'Стратегическое направление',
                                               'field_type': 'document_narrative'})
        self.assertEqual(scope['statement_class'], 'planning_constraint')
        for quote in ('Ориентация на наш персональный сервис.', 'Фокус на гарантированный результат.'):
            self.assertIsNone(classify_obvious_non_business({'content': quote,
                'field_role': 'Стратегическое направление', 'field_type': 'document_narrative'}))


class BoundedDeliveryTests(unittest.TestCase):
    context = {'randomized_acceptance_run': True, 'acceptance_execution_mode': 'bounded_workflow'}

    def test_initial_generation_and_final_repair_have_independent_budgets(self):
        self.assertEqual(attempt_limit(self.context, 7), 1)
        self.assertEqual(final_repair_limit(self.context, 2), 1)
        self.assertEqual(final_repair_limit({'randomized_acceptance_run': True}, 2), 0)
        self.assertEqual(final_repair_limit({}, 2), 2)
        for bad in (-1, True, 1.5):
            with self.assertRaises(ValueError): final_repair_limit(self.context, bad)

    def test_final_pass_cannot_relabel_failed_first_pass_or_erase_repair(self):
        compound = [{'stage':'final_review_result','attempt':1,'status':'BLOCKED'},
                    {'stage':'autonomous_retry','attempt':2},
                    {'stage':'final_review_result','attempt':2,'status':'PASS'}]
        metrics=workflow_outcome_metrics('compound_strategy_calendar',compound)
        self.assertEqual(metrics['first_pass_status'],'FAIL')
        self.assertTrue(metrics['targeted_repair_applied'])
        metrics=workflow_outcome_metrics('standalone_post',
            [{'stage':'text_first_pass_review_result','passed':False}], {'repair_applied':True,'pass':True})
        self.assertEqual(metrics['first_pass_status'],'FAIL')
        self.assertTrue(metrics['targeted_repair_applied'])
        metrics=workflow_outcome_metrics('compound_strategy_calendar',
            [{'stage':'final_review_result','attempt':1,'status':'PASS'}])
        self.assertEqual(metrics['first_pass_status'],'PASS')
        self.assertFalse(metrics['targeted_repair_applied'])

    def test_standalone_repairs_once_and_preserves_failed_first_pass_event(self):
        events=[]; reviews=[]; repairs=[]
        issue={'quote':'Мы даём гарантию.', 'reason':'Нет источника.', 'check':'factual_grounding'}
        def review(req, draft, ctx, **kwargs):
            reviews.append(draft)
            passed=len(reviews)==2
            return {'pass':passed,'checks':{k:passed for k in CHECKS},
                    'issues':[] if passed else [issue],
                    'reviewer_models':dict(zip(CHECKS,['a','b','c','d']))}
        def repair(req,draft,issues,ctx,**kwargs):
            repairs.append((draft,issues)); return 'Общее полезное объяснение. Напишите свой вопрос.'
        result=run_quality_cycle('Напиши пост.',{'action':'write','answer':'Мы даём гарантию.'},
            self.context,reviewer=review,repairer=repair,
            report=lambda name,**details:events.append({'stage':name,**details}))
        self.assertEqual(result['action'],'write')
        self.assertEqual(len(repairs),1)
        self.assertEqual(len(reviews),2)
        self.assertFalse(events[0]['passed'])
        self.assertTrue(result['quality_review']['repair_applied'])
        self.assertEqual(result['quality_review']['previous_issues'],[issue])

    def test_standalone_does_not_repeat_repair_after_second_rejection(self):
        repairs=[]
        def review(req,draft,ctx,**kw):
            return {'pass':False,'issues':[{'quote':draft,'reason':'Не подтверждено.'}]}
        result=run_quality_cycle('Пост.',{'action':'write','answer':'Первый черновик.'},self.context,
            reviewer=review,repairer=lambda *a,**k:repairs.append(1) or 'Второй черновик.')
        self.assertEqual(result['action'],'write_blocked')
        self.assertEqual(repairs,[1])

    def test_compound_repairs_only_cited_slot_once_and_preserves_original_verdict(self):
        prepared=gate_fixtures.FinalEditorialGateTests.prepared()
        calls=[]; events=[]; gates=[]
        issue={'document':'calendar','type':'invented_fact','quote':'Плохое обещание.',
               'reason':'Нет источника.', 'row_context':{'slot':'1/Ср'}}
        def executor(request,context,progress=None):
            calls.append(context)
            self.assertEqual(context['task_mode'],'final_review_repair')
            self.assertEqual(context['final_review_repair_slots'],['1/Ср'])
            self.assertTrue(context['randomized_acceptance_run'])
            return prepared
        def gate(result,request,reviewer):
            gates.append(1)
            out=dict(result);out['content_delivery']=dict(result['content_delivery'])
            out['content_delivery']['independent_editor']={'status':'BLOCKED','review_state':'rejected',
                'issues':[issue],'repair_slots':['1/Ср'],'repair_issues_by_slot':{'1/Ср':[issue]}}
            return out
        with patch('production_pipeline.run_compound_with_retry',return_value=prepared) as initial, \
             patch('production_pipeline.final_quality_gate',side_effect=gate):
            result=run_document_task('request',self.context,execute=executor,progress=events.append,
                                    strategy_reviewer=lambda *a:{'pass':True,'issues':[]})
        self.assertEqual(initial.call_args.kwargs['max_attempts'],1)
        self.assertEqual(len(calls),1)
        self.assertEqual(len(gates),2)
        verdicts=[e for e in events if e['stage']=='final_review_result']
        self.assertEqual([e['attempt'] for e in verdicts],[1,2])
        self.assertTrue(all(e['status']=='BLOCKED' for e in verdicts))
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'BLOCKED')

    def test_probe_leaves_output_room_without_second_request_or_longer_timeout(self):
        import generic_text_review as review
        calls=[]
        def model(payload,**kwargs):
            calls.append((payload,kwargs))
            self.assertGreaterEqual(payload['options']['num_predict'],1024)
            from qualification_fixture import qualification_reply
            return qualification_reply(payload)
        import review_qualification
        with patch.dict(review_qualification._CACHE,{},clear=True), \
             patch('review_qualification.secrets.randbits',return_value=6901), \
             patch('generic_text_review.request_chat',side_effect=model):
            self.assertTrue(review._has_structured_json_contract('unknown-reasoning-model'))
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1]['attempts'],1)
        self.assertEqual(calls[0][1]['timeout'],45)


if __name__=='__main__': unittest.main()
