import copy
import io
import json
import random
import unittest
from unittest.mock import patch

from evidence_segments import sentence_spans
from claim_support import public_copy_findings, adjudicate_factual_findings
from claim_interpretation import bind_interpretation
from independent_acceptance_editor import _inventory_segments
from independent_quality_gate import bind_stage_rejection
from completion_gate import can_close_task
from production_pipeline import run_document_task
from ollama_resilience import request_chat, StructuredResponseError


class EvidenceDeliveryContracts(unittest.TestCase):
    def test_list_sentences_share_exact_catalog_boundaries(self):
        rng = random.Random(5701)
        for index in range(1, 101):
            token = ''.join(rng.choice('абвXYZ🐾') for _ in range(20))
            text = f'{index}. {token}: 27.50 единиц. Ещё одно предложение!'
            spans = [text[a:b] for a,b in sentence_spans(text)]
            public = [r['quote'] for r in public_copy_findings(text)]
            compound = [r['content'] for r in _inventory_segments(text, '')]
            self.assertEqual(spans, public)
            self.assertEqual(spans, compound)
            self.assertEqual(len(spans), 2)
            self.assertTrue(spans[0].startswith(f'{index}. {token}'))
            self.assertIn('27.50', spans[0])
            self.assertNotIn(f'{index}.', spans)

    def test_ordered_table_cell_keeps_marker_with_content(self):
        rows = _inventory_segments('', '| Текст | CTA |\n|---|---|\n|1. Alpha. 2. Beta.|Answer.|')
        self.assertEqual(rows[0]['content'], '1. Alpha.')
        self.assertEqual(rows[1]['content'], '2. Beta.')

    def source_audit(self, text, decision):
        def reply(model, system, user, **kwargs):
            payload = json.loads(user)
            self.assertIn('speech_act', kwargs['json_schema']['properties']['decisions']
                          ['properties']['F001']['required'])
            return {'decisions': {p['finding_id']: dict(decision) for p in payload['findings']}}
        return adjudicate_factual_findings('Сделай полезный материал.', public_copy_findings(text),
            ask=reply, require_semantic_contract=True)

    def test_typed_reader_action_is_not_a_company_service(self):
        # Arbitrary domains and language forms exercise the same role contract.
        for text in ['Выберите желаемую тему.', 'List the files you need.',
                     'Назовите вопросы о посадке растения.', 'Сравните варианты маршрута.']:
            result=self.source_audit(text, {'invitation_kind':'none','statement_class':'commercial_condition',
                'speech_act':'advice', 'assertion_subject':'reader',
                'source_relation':'not_needed', 'source_quote_id':'none',
                'verdict':'unsupported', 'reason':'Действие читателя, без обещания услуги.'})
            self.assertEqual(result['blocking'], [])
            self.assertTrue(result['non_business'])

    def test_typed_neutral_invitation_has_no_company_service_promise(self):
        result=self.source_audit('Ответьте на эти вопросы, чтобы уточнить ваши потребности.', {'statement_class':'business_fact',
            'speech_act':'invitation','assertion_subject':'reader','source_relation':'not_needed','invitation_kind':'communication',
            'source_quote_id':'none','verdict':'unsupported','reason':'Читателю предложено описать вопросы.'})
        self.assertEqual(result['blocking'],[])
        self.assertTrue(result['non_business'])

    def test_editorial_source_cannot_establish_service_promise(self):
        for relation in ('instruction','unrelated','unknown','contradicts'):
            result=self.source_audit('Мы выполним работу без задержек.', {'invitation_kind':'none',
                'statement_class':'business_fact','speech_act':'assertion',
                'assertion_subject':'business','source_relation':relation,
                'source_quote_id':'none','verdict':'supported',
                'reason':'Цитата задаёт редакторскую инструкцию, а не результат услуги.'})
            self.assertEqual(result['supported'], [])
            if relation in ('instruction','contradicts'):
                self.assertEqual(result['blocking'][0]['adjudication'],'unavailable')
                self.assertIn('source_quote_id=none',result['blocking'][0]['reason'])
            else:
                self.assertEqual(result['blocking'][0]['adjudication'],'unsupported')
                self.assertEqual(result['blocking'][0]['adjudication_trace']['source_relation'],relation)

    def test_first_party_cannot_hide_inside_reader_advice(self):
        result=self.source_audit('Мы бесплатно доставим каждый заказ.', {'invitation_kind':'none',
            'statement_class':'general_information','speech_act':'advice',
            'assertion_subject':'reader','source_relation':'not_needed','source_quote_id':'none',
            'verdict':'supported','reason':'Модель ошибочно называет услугу советом читателю.'})
        self.assertEqual(result['non_business'], [])
        self.assertEqual(result['blocking'][0]['adjudication'],'unavailable')

    def test_missing_or_inconsistent_semantic_fields_are_review_outage(self):
        base={'invitation_kind':'none','statement_class':'business_fact','speech_act':'assertion','assertion_subject':'business',
              'source_relation':'entails','source_quote_id':'none','verdict':'supported',
              'reason':'Утверждение требует точного подтверждения.'}
        for key in ('speech_act','assertion_subject','source_relation'):
            value=dict(base);value.pop(key)
            result=self.source_audit('Там выполняют отдельную процедуру.',value)
            self.assertEqual(result['blocking'][0]['adjudication'],'unavailable')
        with self.assertRaises(ValueError):
            bind_interpretation({**base,'assertion_subject':'general'},require=True)

    def test_preflight_failure_stays_blocked_and_addressable(self):
        draft={'action':'compound_documents','documents':[{'kind':'strategy',
            'content':'Strategy with a concrete defect.','valid':False,'reason':'Rejected'}]}
        original=copy.deepcopy(draft)
        issue={'document':'strategy','type':'quality','quote':'concrete defect',
               'reason':'This exact part conflicts with the brief.'}
        result=bind_stage_rejection(draft,[issue])
        self.assertEqual(draft,original)
        self.assertEqual(result['content_delivery']['independent_editor']['issues'],[issue])
        self.assertFalse(can_close_task({'strategy','calendar'},result['content_delivery']))
        self.assertIs(bind_stage_rejection(draft,[{**issue,'quote':'Not in draft'}]),draft)
        outage={**draft,'failure_code':'independent_review_unavailable'}
        self.assertIs(bind_stage_rejection(outage,[issue]),outage)

    def test_partial_strategy_uses_same_bounded_revision_before_calendar_exists(self):
        calls=[];events=[]
        rejected='Strategy with a concrete defect.'
        issue={'document':'strategy','type':'quality','quote':'concrete defect',
               'reason':'This exact part conflicts with the brief.'}
        def preflight(request,body,**kwargs):
            return {'pass':body!=rejected,'issues':[issue] if body==rejected else []}
        def execute(request,state,model=None,progress=None):
            calls.append((model,copy.copy(state)))
            if state.get('task_mode')!='final_review_repair':
                state['strategy_reviewer'](rejected)
                return {'action':'compound_documents','documents':[{'kind':'strategy',
                    'content':rejected,'valid':False,'reason':issue['reason']}]}
            self.assertEqual(state['rejected_documents'][0]['content'],rejected)
            self.assertEqual(state['final_review_issues'],[issue])
            state['strategy_reviewer']('A corrected strategy.')
            return {'action':'compound_documents','documents':[
                {'kind':'strategy','content':'A corrected strategy.','valid':True},
                {'kind':'calendar','content':'A completed calendar.','valid':True}],
                'content_delivery':{'deliverables':{'strategy':'validated','calendar':'validated'},
                    'scope':{'weeks':1,'per_week':1,'planned_total':1},
                    'checked_posts':1,'content_components_checked':dict.fromkeys(
                        ('caption','visual','cta','kpi','audience','measurement','materials'),1),
                    'plan_preflight':{'reviewer_available':True,'unresolved_slots':[]},
                    'task_orchestration':{'completed':True},
                    'rendered_media':'not_requested','unfulfilled':[]}}
        # Real orchestration, with injected generation/review; no live quality claim.
        result=run_document_task('Prepare a strategy and calendar.',{
            'randomized_acceptance_run':True,'acceptance_execution_mode':'bounded_workflow',
            'quality_model_pool':['author','reviser','reviewer']},model='author',review_model='reviewer',
            execute=execute,strategy_reviewer=preflight,
            final_reviewer=lambda *a,**k:{'pass':True,'issues':[]},progress=events.append)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1][0],'reviser')
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'PASS')
        self.assertTrue(any(e['stage']=='autonomous_retry' for e in events))

    def test_cloud_direct_and_legacy_transport_preserve_identical_result(self):
        schema={'type':'object','additionalProperties':False,'properties':{
            'decisions':{'type':'object'}},'required':['decisions']}
        value={'decisions':{'A':{'text':'"quotes"\n🐾 {[]}'} }}
        for arguments in (value,{'result':value},json.dumps(value)):
            response={'message':{'content':'','tool_calls':[{'function':{
                'name':'return_structured_result','arguments':arguments}}]}}
            seen=[]
            def transport(req,timeout):
                seen.append(json.loads(req.data))
                return io.BytesIO(json.dumps(response).encode())
            with patch('ollama_resilience.urllib.request.urlopen',side_effect=transport):
                result=request_chat({'model':'arbitrary:cloud','format':schema,
                    'messages':[{'role':'user','content':'Return data'}]},stage='typed result',attempts=1)
            self.assertEqual(json.loads(result['message']['content']),value)
            self.assertEqual(seen[0]['tools'][0]['function']['parameters'],schema)

    def test_cloud_unknown_fields_and_ambiguous_tool_calls_stay_errors(self):
        schema={'type':'object','additionalProperties':False,'properties':{
            'decisions':{'type':'object'}},'required':['decisions']}
        for args in ({'unissued':{}},{'decisions':{},'extra':1},{'result':{},'decisions':{}}):
            raw={'message':{'tool_calls':[{'function':{'name':'return_structured_result',
                'arguments':args}}]}}
            with patch('ollama_resilience.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(raw).encode())):
                with self.assertRaises(StructuredResponseError):
                    request_chat({'model':'arbitrary:cloud','format':schema,'messages':[]},
                                 stage='typed result',attempts=1)

    def test_semantic_field_named_result_is_not_a_transport_wrapper(self):
        schema={'type':'object','additionalProperties':False,'properties':{
            'result':{'type':'object'}},'required':['result']}
        value={'result':{'meaning':'This is the requested field, not an envelope.'}}
        response={'message':{'tool_calls':[{'function':{
            'name':'return_structured_result','arguments':value}}]}}
        with patch('ollama_resilience.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(response).encode())):
            answer=request_chat({'model':'arbitrary:cloud','format':schema,'messages':[]},
                                stage='typed result',attempts=1)
        self.assertEqual(json.loads(answer['message']['content']),value)


if __name__=='__main__':
    unittest.main()
