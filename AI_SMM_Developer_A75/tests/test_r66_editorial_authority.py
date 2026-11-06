"""Regression of host decisions, speech acts, cross-field evidence and form review.

Injected judgements validate engineering behaviour, never live semantic accuracy.
"""
import json
import unittest
from unittest.mock import patch
from artifact_review import (ArtifactReviewContract, resolve_editorial_approval,
                             validate_reason_reference, ReviewContractError)
from claim_interpretation import bind_interpretation
from claim_support import adjudicate_factual_findings, public_copy_findings
import generic_text_review as generic
import independent_acceptance_editor as editor
from test_r62_review_boundaries import with_changes
from test_r65_review_evidence import response_for


class EditorialAuthority(unittest.TestCase):
    def test_validated_negative_finding_always_overrides_positive_model_flag(self):
        self.assertFalse(resolve_editorial_approval(True,[{'a':'finding'}]))
        self.assertFalse(resolve_editorial_approval(False,[{'a':'finding'}]))
        self.assertTrue(resolve_editorial_approval(True,[]))
        for flag,issues in [(False,[]),('true',[]),(True,None)]:
            with self.assertRaises(ValueError):resolve_editorial_approval(flag,issues)

    def test_generic_review_returns_valid_negative_without_spending_a_correction(self):
        for check in ('requirements','completeness','format_and_tone'):
            def chat(body,**kw):
                value=response_for(body);value['pass']=True
                return {'message':{'content':json.dumps(value)}}
            with patch.object(generic,'request_chat',side_effect=chat) as call:
                result=generic.review_dimension('Write a post.','A concrete defect.',{},check,'judge')
            self.assertEqual(call.call_count,1)
            self.assertFalse(result['pass']);self.assertTrue(result['model_pass'])
            self.assertEqual(result['issues'][0]['quote'],'A concrete defect.')

    def test_positive_flag_does_not_hide_invalid_evidence_or_an_empty_rejection(self):
        def chat(body,**kw):
            value=response_for(body);value['pass']=True;value['issues'][0]['evidence_id']='not-issued'
            return {'message':{'content':json.dumps(value)}}
        with patch.object(generic,'request_chat',side_effect=chat) as call:
            with self.assertRaises(ReviewContractError):generic.review_dimension('Write.','Text.',{},'completeness','judge')
        self.assertEqual(call.call_count,2)
        with patch.object(generic,'request_chat',return_value={'message':{'content':'{"pass":false,"issues":[]}'}}):
            with self.assertRaises(ReviewContractError):generic.review_dimension('Write.','Text.',{},'completeness','judge')

    def test_publication_and_bundle_boundaries_use_the_same_restrictive_authority(self):
        contract=ArtifactReviewContract.create('Write.',{'caption':'Exact document content.'})
        row={'field':'caption','evidence_id':contract.spans[0].ident,'kind':'quality','requirement_id':'none','reason':'Concrete verified wording problem.'}
        findings,_=contract.validate({'approved':True,'issues':[row]})
        self.assertEqual(findings[0]['quote'],'Exact document content.')
        result=editor.parse_verdict(json.dumps({'pass':True,'issues':[{
            'document':'strategy','type':'quality','quote':'Exact document content.','reason':'Concrete verified wording problem.'}]}),strategy='Exact document content.',calendar='')
        self.assertFalse(result['pass']);self.assertEqual(len(result['issues']),1)

    def test_reason_cannot_cite_another_field_under_any_defect_type(self):
        strategy='# Strategy\n\nA complete unrelated strategic recommendation.'
        calendar='# Calendar\n\nA reader-facing story about a different subject.'
        catalog,_=editor._editor_evidence_index(strategy,calendar)
        ref=next(k for k in catalog if k.startswith('calendar:'))
        for kind in ('invented_fact','unsafe_claim','quality','contradiction','other'):
            issue={'document':'calendar','type':kind,'evidence_id':ref,
                   'reason':"The text 'A complete unrelated strategic recommendation.' is incorrect."}
            with self.assertRaisesRegex(ValueError,'другое поле'):
                editor.parse_verdict(json.dumps({'pass':False,'issues':[issue]}),strategy=strategy,calendar=calendar,
                    request='Review the documents.',require_claim=True,host_evidence_only=True,_evidence_catalog=catalog)
        validate_reason_reference("The request says 'A complete unrelated strategic recommendation.'.",
            'Different text.',catalog.values(),'Include: A complete unrelated strategic recommendation.')

    def test_explicit_related_evidence_preserves_legitimate_cross_field_comparison(self):
        strategy='# Strategy\n\nThe authorized plan specifies a different duration.'
        calendar='# Calendar\n\nThe caption promises an incompatible duration.'
        catalog,_=editor._editor_evidence_index(strategy,calendar)
        first=next(k for k in catalog if k.startswith('strategy:'))
        second=next(k for k in catalog if k.startswith('calendar:'))
        issue={'document':'calendar','type':'contradiction','evidence_id':second,
               'related_evidence_ids':[first],
               'reason':"The caption contradicts 'The authorized plan specifies a different duration.'."}
        result=editor.parse_verdict(json.dumps({'pass':True,'issues':[issue]}),strategy=strategy,calendar=calendar,
            request='Review.',require_claim=True,host_evidence_only=True,_evidence_catalog=catalog)
        self.assertFalse(result['pass'])
        self.assertEqual(result['issues'][0]['related_quotes'],[catalog[first]])
        for refs in ([first,first],['not_issued'],None):
            issue['related_evidence_ids']=refs
            with self.assertRaises(ValueError):editor.parse_verdict(json.dumps({'pass':False,'issues':[issue]}),
                strategy=strategy,calendar=calendar,request='Review.',require_claim=True,
                host_evidence_only=True,_evidence_catalog=catalog)

    def test_related_evidence_survives_generic_aggregate_and_revision(self):
        from text_task_contract import TextRevisionContract
        fields={'primary':'A contradictory selected claim.','context':'A distinct contextual statement.'}
        body='\n\n'.join(fields.values())
        def chat(payload,**kw):
            data=json.loads(payload['messages'][-1]['content'])
            first=next(r for r in data['review_evidence'] if r['field']=='primary')
            second=next(r for r in data['review_evidence'] if r['field']=='context')
            issue={'evidence_id':first['evidence_id'],'related_evidence_ids':[second['evidence_id']],
                'reason':"The selected claim contradicts 'A distinct contextual statement.'.",'severity':'major',
                'requirement_id':data['requirement_bindings'][0]['id'],
                'failure_type':'required_content_missing'}
            return {'message':{'content':json.dumps({'pass':True,'issues':[issue]})}}
        with patch.object(generic,'request_chat',side_effect=chat):
            result=generic.review_dimension('Review.',body,{},'completeness','judge',artifact_fields=fields)
        checks=dict.fromkeys(generic.CHECKS,True);checks['completeness']=False
        aggregated=generic.validate_verdict({'pass':False,'checks':checks,'issues':result['issues']},body)
        revision=TextRevisionContract.create(body,aggregated['issues'])
        self.assertEqual(revision.findings[0]['related_quotes'],[fields['context']])
        self.assertTrue(revision.findings[0]['related_evidence_ids'])

    def test_neutral_social_expression_is_non_business_without_a_source(self):
        for act,kind in [('social_expression','none'),('invitation','communication')]:
            decision=with_changes(speech_act=act,assertion_subject='reader',source_relation='not_needed',
                invitation_kind=kind,verdict='supported',source_quote_id='none')
            output=bind_interpretation(decision,require=True)
            self.assertIn(output['statement_class'],('audience_prompt','invitation_cta'))
            calls=[]
            def ask(*args,**kw):calls.append(1);return {'decisions':{'F001':decision}}
            result=adjudicate_factual_findings('Write a post.',public_copy_findings('Hello, readers of the project!'),
                ask=ask,require_semantic_contract=True)
            self.assertFalse(result['blocking']);self.assertEqual(len(result['non_business']),1)

    def test_inconsistent_invitation_kind_is_unavailable_until_corrected_not_a_business_fact(self):
        calls=[]
        def ask(*args,**kw):
            calls.append(1)
            return {'decisions':{'F001':with_changes(speech_act='invitation',assertion_subject='reader',
                source_relation='not_needed',invitation_kind='none' if len(calls)==1 else 'communication',
                verdict='supported')}}
        result=adjudicate_factual_findings('Write a post.',public_copy_findings('Hello to readers of the project!'),
            ask=ask,require_semantic_contract=True)
        self.assertEqual(len(calls),2);self.assertFalse(result['blocking']);self.assertEqual(len(result['non_business']),1)

    def test_company_promises_cannot_inherit_social_or_communication_exemption(self):
        for act in ('social_expression','invitation'):
            decision=with_changes(speech_act=act,assertion_subject='reader',source_relation='not_needed',
                invitation_kind='none' if act=='social_expression' else 'communication',verdict='supported')
            def ask(*a,**kw):return {'decisions':{'F001':decision}}
            result=adjudicate_factual_findings('Write a post.',public_copy_findings('Мы гарантируем результат каждому клиенту.'),
                ask=ask,require_semantic_contract=True)
            self.assertTrue(result['blocking']);self.assertFalse(result['non_business'])

    def test_dedicated_format_review_preserves_document_and_slot_of_the_reported_field(self):
        strategy='# Стратегия\n\n## Цели\nA complete English recommendation for a Russian request.'
        calendar='# Календарь\n\nРусский текст для читателя.'
        def chat(body,**kw):
            user=json.loads(body['messages'][-1]['content'])
            self.assertEqual(user['review_dimension'],'format_and_tone')
            item=next(r for r in user['review_evidence'] if 'English recommendation' in r['text'])
            self.assertTrue(item['field'].startswith('strategy:'))
            value=response_for(body);value['issues'][0].update(evidence_id=item['evidence_id'],
                requirement_id='G_language',failure_type='style_mismatch',
                reason='The requested Russian text is predominantly written in English.')
            return {'message':{'content':json.dumps(value)}}
        with patch.object(generic,'request_chat',side_effect=chat):
            findings=editor._review_artifact_form('Напиши на русском языке.',strategy,calendar,model='judge')
        self.assertEqual(findings[0]['document'],'strategy');self.assertEqual(findings[0]['type'],'quality')
        self.assertIn('English recommendation',findings[0]['quote'])

    def test_format_finding_targets_publication_field_and_document_repair_keeps_provenance(self):
        from test_stage69_scope import good_post
        from publication_writer import week_to_markdown,repair_field_targets
        from document_recovery import patch_document
        slot={'week':1,'day':'Вт','topic':'Тема','format':'Пост','platform':'Telegram','goal':'Вопросы','audience':'Читатели'}
        post=good_post(slot);post['cta']='Share your own question with the audience.'
        calendar=week_to_markdown([post],[slot])
        def chat(payload,**kw):
            data=json.loads(payload['messages'][-1]['content'])
            target=next(row for row in data['review_evidence'] if row['field_type']=='cta')
            issue=response_for(payload)['issues'][0]
            issue.update(evidence_id=target['evidence_id'],requirement_id='G_language',failure_type='style_mismatch')
            return {'message':{'content':json.dumps({'pass':False,'issues':[issue]})}}
        with patch.object(generic,'request_chat',side_effect=chat):
            findings=editor._review_artifact_form('Пиши на русском.', '',calendar,model='judge')
        self.assertEqual(findings[0]['field'],'cta')
        self.assertEqual(repair_field_targets(findings),('cta',))
        self.assertEqual(findings[0]['row_context']['slot'],'1/Вт')
        finding={'document':'strategy','type':'quality','quote':'Wrong language.',
            'reason':'The requested language differs.','field':'document_narrative',
            'requirement_id':'G_language','requirement_quote':'Use the requested language.',
            'review_signature':'a'*64,'related_evidence_ids':['other_id'],'related_quotes':['Context.']}
        def ask(model,system,user,**kw):
            data=json.loads(user);kept=data['structured_findings'][0]
            for key in ('field','requirement_id','requirement_quote','review_signature','related_evidence_ids','related_quotes'):
                self.assertEqual(kept[key],finding[key])
            return json.dumps({'changes':[{'line_id':'L00001','text':'Исправленный язык.'}]})
        output=patch_document(ask,'author','Исправь язык.','Wrong language.','Language defect',
            findings=[finding],constrained=True)
        self.assertIn('Исправленный язык.',output)

    def test_strategy_cannot_continue_from_main_pass_until_explicit_format_review_passes(self):
        from strategy_recovery import produce_checked_strategy
        wrong='A complete English explanation for a Russian reader.'
        right='Полное объяснение на запрошенном русском языке.'
        observed=[];saved=[]
        def chat(body,**kw):
            data=json.loads(body['messages'][-1]['content']);observed.append(data)
            if wrong not in data['draft_to_review']:
                return {'message':{'content':'{"pass":true,"issues":[]}'}}
            issue=response_for(body)['issues'][0]
            issue.update(requirement_id='G_language',failure_type='style_mismatch',
                reason='The requested Russian explanation is written in English.')
            return {'message':{'content':json.dumps({'pass':True,'issues':[issue]})}}
        with patch.object(editor,'_call_editor_once',return_value={'pass':True,'issues':[]}), \
             patch.object(editor,'_inventory_business_assertions',return_value=[]), \
             patch.object(generic,'request_chat',side_effect=chat), \
             patch('strategy_recovery.strategy_integrity_issues',return_value=[]):
            result=produce_checked_strategy('Разработай стратегию.',1,lambda *a:wrong,
                lambda *a:(True,''),attempts=2,repair=lambda *a:right,
                review=lambda body:editor.call_strategy_editor('Разработай стратегию.',body,require_inventory=True),
                checkpoint=lambda *a:saved.append(a))
        self.assertTrue(result[1]);self.assertIn(right,result[0])
        self.assertFalse(saved[0][1]);self.assertTrue(saved[-1][1])
        self.assertGreaterEqual(len(observed),2)

    def test_format_check_windows_are_bounded_and_unavailable_state_survives_source_success(self):
        strategy='# Стратегия\n'+('A distinct complete sentence about planning.\n'*650)
        calls=[]
        def form(request,st,ca,**kw):calls.append(len(st)+len(ca));return []
        with patch.object(editor,'_call_editor_once',return_value={'pass':True,'issues':[]}), \
             patch.object(editor,'_review_artifact_form',side_effect=form), \
             patch.object(editor,'_inventory_business_assertions',return_value=[]):
            result=editor.call_editor('Подготовь стратегию.',strategy,'# Календарь\nОдин пост.',require_inventory=True)
        self.assertTrue(result['pass']);self.assertGreater(len(calls),1)
        self.assertTrue(all(n<editor.EDITOR_CHARACTER_BUDGET for n in calls))
        def unavailable(*a,**kw):raise TimeoutError('Format review offline')
        with patch.object(editor,'_call_editor_once',return_value={'pass':True,'issues':[]}), \
             patch.object(editor,'_review_artifact_form',side_effect=unavailable), \
             patch.object(editor,'_inventory_business_assertions',return_value=[{
                'document':'strategy','type':'invented_fact','quote':'Specific statement.','reason':'Source check needed.'}]), \
             patch('claim_support.adjudicate_factual_findings',return_value={'supported':[],'blocking':[]}):
            result=editor.call_editor('Write.','Specific statement.','# Calendar\nA post.',require_inventory=True)
        self.assertFalse(result['pass']);self.assertEqual(result['review_state'],'unavailable')


if __name__=='__main__':unittest.main()
