"""Injected known-answer replies for other boundary tests, not live model QA."""
import json
from review_qualification import build_controls


def qualification_reply(payload,seed=6901,wrong_kind=None):
    value=json.loads(payload['messages'][-1]['content'])
    _,fields,_,oracle=build_controls(seed)
    assert value['artifact_fields']==fields,'The test must seed the randomized probe explicitly'
    decisions={}
    for row in value['review_findings']:
        expected=oracle[row['finding']['field']];change=expected['needs_change']
        if expected['kind']==wrong_kind:change=not change
        decisions[row['id']]={'action':'correct_artifact' if change else 'keep_artifact',
            'remedy_scope':'current' if change else 'none',
            'required_change':'Correct the specific mismatch with the issued requirement.' if change else '',
            'reason':'The complete current artifact and issued requirement establish this control decision.'}
    return {'message':{'content':json.dumps({'decisions':decisions})}}
