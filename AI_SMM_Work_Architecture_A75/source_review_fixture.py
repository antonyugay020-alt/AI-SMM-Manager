"""Explicit structured source decisions for tests of other review dimensions."""
import json


def general_source_reply(payload):
    user = json.loads(payload['messages'][1]['content'])
    return {'message': {'content': json.dumps({'decisions': {
        item['finding_id']: {'invitation_kind':'none','statement_class': 'general_information','speech_act':'assertion','assertion_subject':'general','source_relation':'not_needed',
            'verdict': 'supported', 'source_quote_id': 'none',
            'reason': 'Общая информация без утверждения о компании.'}
        for item in user['findings']}})}}
