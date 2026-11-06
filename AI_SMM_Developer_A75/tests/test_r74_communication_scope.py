"""Candidate routing is not proof of a company actor. Injected model decisions."""
import copy
import unittest

from assertion_scope import requires_business_assertion_review,requires_business_fact_review
from claim_support import adjudicate_factual_findings,public_copy_findings


def communication(**changes):
    value={'statement_class':'invitation_cta','speech_act':'invitation','assertion_subject':'reader',
        'invitation_kind':'communication','source_relation':'not_needed','source_quote_id':'none',
        'verdict':'supported','reason':'The reader is invited to communicate, with no company action promised.'}
    value.update(changes);return value


def audit(text,decision):
    rows=public_copy_findings(text);calls=[]
    def ask(model,system,user,**kw):
        import json
        data=json.loads(user);calls.append(data)
        return {'decisions':{row['finding_id']:copy.deepcopy(decision) for row in data['findings']}}
    out=adjudicate_factual_findings('Write an explanation and invite readers to ask a question.',rows,
                                  ask=ask,require_semantic_contract=True)
    return out,calls


class CommunicationScope(unittest.TestCase):
    def test_received_neutral_invitation_has_one_completed_source_decision(self):
        text='Если вы хотите узнать больше о наших услугах или задать вопрос, не стесняйтесь обращаться к нам.'
        row=public_copy_findings(text)[0]
        self.assertTrue(requires_business_fact_review(row))
        self.assertTrue(requires_business_assertion_review(row))
        self.assertFalse(requires_business_assertion_review(row,communication()))
        out,calls=audit(text,communication())
        self.assertEqual(len(calls),1)
        self.assertEqual(out['blocking'],[]);self.assertEqual(out['supported'],[])
        self.assertEqual(out['non_business'][0]['quote'],text)
    def test_ownership_questions_across_named_topics_remain_candidates_not_company_actors(self):
        for noun in ('материалах','проектах','услугах','публикациях','идеях','сообщениях'):
            text=f'Если хотите узнать больше о наших {noun}, задайте свой вопрос.'
            out,calls=audit(text,communication())
            self.assertEqual(out['blocking'],[],text);self.assertEqual(len(calls),1)
        text='If you want to learn about our work, share your question.'
        out,calls=audit(text,communication())
        self.assertEqual(out['blocking'],[]);self.assertEqual(len(calls),1)
    def test_unknown_or_inconsistent_decisions_do_not_bypass_source_scope(self):
        row=public_copy_findings('Уточните вопрос о наших предложениях.')[0]
        changes=[{'statement_class':'business_fact'},{'speech_act':'assertion'},
            {'assertion_subject':'business'},{'invitation_kind':'service_access'},
            {'source_relation':'entails'},{'source_quote_id':'foreign'},{'verdict':'uncertain'}]
        for change in changes:
            self.assertTrue(requires_business_assertion_review(row,communication(**change)),change)
        self.assertTrue(requires_business_assertion_review(row,{}))
    def test_company_promises_cannot_hide_behind_reader_communication(self):
        for text in ('Спросите о наших предложениях — мы выполним заказ.',
                     'Ask about our work; we will deliver a result.',
                     'У нас есть специальное предложение, задайте вопрос.',
                     'Our team guarantees success.',
                     'Наш сервис выполняет каждую операцию вручную.',
                     'Ask about our work, our team will complete the operation.',
                     'Задайте вопрос, наши специалисты выполнят каждый этап.'):
            out,_=audit(text,communication())
            self.assertTrue(out['blocking'],text)
            self.assertFalse(out['supported']);self.assertFalse(out['non_business'])
    def test_physical_visits_still_require_service_evidence(self):
        out,_=audit('Посетите нашу мастерскую, чтобы получить обслуживание.',communication())
        self.assertTrue(out['blocking']);self.assertFalse(out['non_business'])
    def test_service_access_and_business_roles_preserve_the_source_gate(self):
        for decision in [communication(invitation_kind='service_access',verdict='unsupported'),
                         communication(statement_class='business_fact',speech_act='assertion',
                                       assertion_subject='business',invitation_kind='none',
                                       source_relation='unrelated',verdict='unsupported')]:
            out,_=audit('Уточните вопрос о наших предложениях.',decision)
            self.assertTrue(out['blocking']);self.assertFalse(out['non_business'])
    def test_first_party_assertion_disguised_as_general_information_still_blocks(self):
        decision=communication(statement_class='general_information',speech_act='assertion',
                               assertion_subject='general',invitation_kind='none')
        out,_=audit('Our team checks every request twice.',decision)
        self.assertTrue(out['blocking']);self.assertFalse(out['non_business'])
    def test_input_decision_and_source_quotes_are_immutable(self):
        text='Если хотите обсудить наши материалы, задайте вопрос.'
        decision=communication();original=copy.deepcopy(decision)
        out,_=audit(text,decision)
        self.assertEqual(decision,original)
        self.assertEqual(out['non_business'][0]['quote'],text)
