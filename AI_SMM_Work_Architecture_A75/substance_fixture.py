"""Explicit offline opinions for model-gateway fixtures; never production code."""
import copy
import json
from unittest.mock import patch
from independent_acceptance_editor import call_substance_reader as native_substance_reader


def assessed_reply(payload,answer):
    """Upgrade old injected opinions to the current requested schema only."""
    schema=payload.get('format') or (payload.get('tools') or [{'function':{'parameters':{}}}])[0]['function']['parameters']
    if isinstance(schema,dict) and set(schema.get('properties',{}))=={'result'}:
        schema=schema['properties']['result']
    if not isinstance(schema,dict) or 'assessments' not in schema.get('properties',{}):return answer
    contents=[message['content'] for message in payload['messages'] if message['role']=='user']
    content=next((value for value in contents if 'QUALITY_CRITERIA_JSON:\n' in value),contents[0])
    if 'QUALITY_CRITERIA_JSON:\n' in content:
        manifest=json.loads(content.split('QUALITY_CRITERIA_JSON:\n')[-1])
        checks=manifest['checks'];controls=manifest['controls']
    else:
        manifest=json.loads(content);checks=manifest.get('quality_criteria',{});controls=manifest.get('quality_controls',{})
    result=copy.deepcopy(answer)
    if not isinstance(result,dict) or not isinstance(result.get('issues'),list):return result
    for issue in result['issues']:
        ref=issue.get('evidence_id')
        options=[key for key,check in checks.items() if ref in check['evidence_ids']]
        issue.setdefault('criterion_id',options[0] if options and (issue.get('type')=='quality' or 'severity' in issue) else 'none')
    result['assessments']={key:{'status':'defect' if any(issue.get('criterion_id')==key for issue in result['issues']) else 'satisfied',
        'evidence_id':next((issue['evidence_id'] for issue in result['issues'] if issue.get('criterion_id')==key),check['evidence_ids'][0]),
        'reason':'Injected engineering opinion for '+check['criterion']+' in '+check['unit_id']+'.'}
        for key,check in checks.items()}
    result['quality_controls']={key:all(point in row['candidate'] for point in row['source_points'])
        and [row['candidate'].find(point) for point in row['source_points']]==sorted(
            row['candidate'].find(point) for point in row['source_points']) for key,row in controls.items()}
    return result


def assessed_reader(*args,**kwargs):
    """Explicit injected reader opinion for pre-existing orchestration fixtures."""
    def reply(payload,**kw):
        return {'message':{'content':json.dumps(assessed_reply(payload,{'pass':True,'issues':[]}))}}
    with patch('independent_acceptance_editor.request_chat',side_effect=reply):
        return native_substance_reader(*args,**kwargs)
