"""Semantic scope is model-owned; exact edit addresses are host-owned.

Injected replies verify the boundary without asserting live model quality.
"""
import copy
import io
import json
import random
import unittest
from unittest.mock import Mock, patch

from ollama_resilience import request_chat
from qualification_fixture import qualification_reply
from review_arbitration import adjudicate_findings, FindingReviewUnavailable
import review_qualification as qualification
from text_task_contract import TextRevisionContract


def decision(action='correct_artifact', scope='current'):
    return {'action': action, 'remedy_scope': scope,
            'required_change': 'Clarify the specific unsupported inference.' if action == 'correct_artifact' else '',
            'reason': 'The unchanged artifact and complete requirement establish the relevant scope.'}


def issue(field, text='A precise unchanged public text.', pending=()):
    return {'field': field, 'quote': text, 'reason': 'A specific alleged content defect.',
            'review_scope': {'available_artifacts': ['current artifact'], 'pending_artifacts': list(pending)}}


class HostScopeBinding(unittest.TestCase):
    def test_exact_addresses_bind_without_reproduction_for_arbitrary_host_names(self):
        rng = random.Random(7701)
        names = ['none', 'current', 'pending', 'section/поле:二', 'строка_2']
        names += [''.join(rng.choice('абвxyz二/:-_') for _ in range(30)) for _ in range(25)]
        for field in names:
            original = issue(field)
            snapshot = copy.deepcopy(original)
            def ask(model, system, user, *, json_schema):
                prop = json_schema['properties']['decisions']['properties']['A001']
                self.assertNotIn('remedy_target', prop['properties'])
                self.assertEqual(prop['properties']['remedy_scope']['enum'], ['none', 'current'])
                return {'decisions': {'A001': decision()}}
            confirmed, dismissed = adjudicate_findings(ask, 'judge', 'Write.', {field: original['quote']}, [original])
            self.assertFalse(dismissed)
            self.assertEqual(confirmed[0]['finding_validation']['remedy_target'], field)
            self.assertEqual(confirmed[0]['finding_validation']['remedy_scope'], 'current')
            self.assertEqual(original, snapshot)

    def test_current_is_bound_per_finding_and_cannot_select_a_sibling(self):
        fields = {'opaque first': 'First unchanged text.', 'opaque second': 'Second unchanged text.'}
        findings = [issue(name, text) for name, text in fields.items()]
        def ask(model, system, user, **kw):
            rows = json.loads(user)['review_findings']
            return {'decisions': {row['id']: decision() for row in reversed(rows)}}
        confirmed, _ = adjudicate_findings(ask, 'judge', 'Write.', fields, findings)
        self.assertEqual([row['finding_validation']['remedy_target'] for row in confirmed], list(fields))

    def test_pending_scope_preserves_dismissal_without_inventing_an_edit_address(self):
        row = issue('opaque current', pending=['future A', 'future B'])
        confirmed, dismissed = adjudicate_findings(lambda *a, **kw: {'decisions': {
            'A001': decision('keep_artifact', 'pending')}}, 'judge', 'Write.',
            {'opaque current': row['quote']}, [row])
        self.assertFalse(confirmed)
        self.assertEqual(dismissed[0]['finding_validation']['remedy_scope'], 'pending')
        self.assertEqual(dismissed[0]['finding_validation']['remedy_target'], 'none')
        self.assertEqual(dismissed[0]['review_scope']['pending_artifacts'], ['future A', 'future B'])

    def test_pending_unknown_ambiguous_and_inconsistent_scopes_cannot_authorize_edits(self):
        row = issue('current field', pending=['future'])
        invalid = [decision(scope='pending'), decision(scope='none'), decision(scope='foreign'),
                   decision('review_unavailable', 'current'),
                   {**decision(), 'remedy_target': 'current field'},
                   {**decision(), 'required_change': ''}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(FindingReviewUnavailable):
                adjudicate_findings(lambda *a, **kw: {'decisions': {'A001': value}},
                    'judge', 'Write.', {'current field': row['quote']}, [row])
        with self.assertRaises(FindingReviewUnavailable):
            adjudicate_findings(lambda *a, **kw: {'decisions': {'A001': decision('keep_artifact', 'pending')}},
                'judge', 'Write.', {'current field': row['quote']}, [issue('current field')])

    def test_legacy_exact_addresses_remain_strict_and_typos_are_not_inferred(self):
        row = issue('структура_2_random')
        for target, accepted in [('структура_2_random', True), ('поле_2_random', False),
                                 ('структура_2_other', False), ('структура_2_random ', False)]:
            value = decision()
            value.pop('remedy_scope')
            value['remedy_target'] = target
            call = lambda: adjudicate_findings(lambda *a, **kw: {'decisions': {'A001': value}},
                'judge', 'Write.', {row['field']: row['quote']}, [row])
            if accepted:
                confirmed, _ = call()
                self.assertEqual(confirmed[0]['finding_validation']['remedy_target'], row['field'])
            else:
                with self.assertRaises(FindingReviewUnavailable):
                    call()
        # Legacy "none" is a no-address marker even if a host field has that
        # literal name; current edits are unambiguous in the new scope format.
        row = issue('none')
        value = decision('review_unavailable', 'none')
        value['remedy_target'] = value.pop('remedy_scope')
        with self.assertRaises(FindingReviewUnavailable) as caught:
            adjudicate_findings(lambda *a, **kw: {'decisions': {'A001': value}},
                'judge', 'Write.', {'none': row['quote']}, [row])
        self.assertTrue(caught.exception.contract_valid)

    def test_bound_proof_survives_revision_without_mutable_aliases(self):
        row = issue('standalone')
        confirmed, _ = adjudicate_findings(lambda *a, **kw: {'decisions': {'A001': decision()}},
            'judge', 'Write.', {'standalone': row['quote']}, [row])
        confirmed[0]['check'] = 'requirements'
        revision = TextRevisionContract.create(row['quote'], confirmed)
        confirmed[0]['finding_validation']['remedy_target'] = 'changed'
        self.assertEqual(revision.findings[0]['finding_validation']['remedy_target'], 'standalone')
        self.assertEqual(revision.findings[0]['finding_validation']['remedy_scope'], 'current')

    def test_actual_local_and_tool_gateways_return_scope_and_host_binds_address(self):
        for model in ('arbitrary-local:7b', 'arbitrary-cloud:cloud'):
            calls = []
            def wire(req, timeout):
                self.assertEqual(req.full_url, 'http://127.0.0.1:11434/api/chat')
                body = json.loads(req.data)
                schema = (body['tools'][0]['function']['parameters'] if body.get('tools') else body['format'])
                prop = schema['properties']['decisions']['properties']['A001']
                self.assertIn('remedy_scope', prop['required'])
                answer = {'decisions': {'A001': decision()}}
                calls.append(body)
                message = ({'content': '', 'tool_calls': [{'function': {
                    'name': 'return_structured_result', 'arguments': answer}}]}
                    if body.get('tools') else {'content': json.dumps(answer)})
                return io.BytesIO(json.dumps({'message': message, 'done': True, 'done_reason': 'stop'}).encode())
            def ask(reviewer, system, user, *, json_schema):
                result = request_chat({'model': reviewer, 'format': json_schema, 'stream': False,
                    'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]},
                    stage='scope binding', attempts=1)
                return result['message']['content']
            row = issue('opaque/address/二')
            with patch('urllib.request.urlopen', side_effect=wire):
                confirmed, _ = adjudicate_findings(ask, model, 'Write.', {row['field']: row['quote']}, [row])
            self.assertEqual(len(calls), 1)
            self.assertEqual(confirmed[0]['finding_validation']['remedy_target'], row['field'])

    def test_six_controls_still_require_six_correct_answers_and_new_protocol(self):
        good = qualification.probe_qualification('host_scope_judge', seed=6901,
            transport=lambda p, **kw: qualification_reply(p), force=True)
        self.assertTrue(good['pass'])
        self.assertEqual(good['semantic_controls_correct'], 6)
        self.assertEqual(good['model_calls'], 1)
        for kind in ('scope_local', 'scope_pending', 'literal_present', 'literal_missing',
                     'source_supported', 'source_changed'):
            sender = Mock(side_effect=lambda p, **kw: qualification_reply(p, wrong_kind=kind))
            bad = qualification.probe_qualification('host_scope_judge', seed=6901, transport=sender, force=True)
            self.assertFalse(bad['pass'])
            self.assertEqual(bad['semantic_controls_correct'], 5)
            self.assertEqual(sender.call_count, 1)
        old = {**good, 'protocol': 'review_qualification_scope_targets_r69_v1'}
        self.assertFalse(qualification._valid_record(old, old['model']))
