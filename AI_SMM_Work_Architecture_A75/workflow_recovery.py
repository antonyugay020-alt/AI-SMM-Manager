"""Host-owned recovery targets for every unfinished compound production stage.

Draft artifacts remain unapproved. IDs and hashes bind a finding to the exact
plan or publication that failed, even when the calendar has no finished rows.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re


def artifact_hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':')).encode()).hexdigest()


def capture_failures(signature, required_ids, slots, blocked_plan, review_available,
                     rejected_posts, accepted_ids):
    if review_available is not True:
        return []
    by_slot={f"{s.get('week')}/{s.get('day')}":s for s in slots if isinstance(s,dict)}
    allowed=set(required_ids)-set(accepted_ids)
    records=[]
    for ident,reason in blocked_plan.items() if review_available else []:
        if ident in allowed and ident in by_slot and isinstance(reason,str) and reason.strip():
            records.append({'stage':'plan','slot':ident,'artifact':copy.deepcopy(by_slot[ident]),
                'reason':reason,'operation':'revise_plan'})
    for ident,item in rejected_posts.items():
        if ident not in allowed or ident in blocked_plan or not isinstance(item,dict):
            continue
        post=item.get('post');issues=item.get('issues')
        if (not isinstance(post,dict) or post.get('id')!=ident
                or not isinstance(issues,list) or not issues):
            continue
        if ident in by_slot and any(isinstance(i,dict) and i.get('field')=='topic' for i in issues):
            records.append({'stage':'plan','slot':ident,'artifact':copy.deepcopy(by_slot[ident]),
                'reason':'; '.join(str(i.get('reason') or '') for i in issues if isinstance(i,dict)),
                'operation':'revise_plan'})
            continue
        records.append({'stage':'publication','slot':ident,'artifact':copy.deepcopy(item),
            'reason':'; '.join(str(i.get('reason') or i) if isinstance(i,dict) else str(i)
                              for i in issues),'operation':'revise_publication'})
    for record in records:
        record['task_signature']=signature
        record['artifact_sha256']=artifact_hash(record['artifact'])
    return records


def verified_failures(result):
    if isinstance(result,dict) and result.get('failure_code')=='independent_review_unavailable':
        return []
    signature=result.get('task_signature') if isinstance(result,dict) else None
    if not isinstance(signature,str) or not re.fullmatch(r'[0-9a-f]{64}',signature):
        return []
    report=result.get('content_delivery',{})
    if not isinstance(report,dict):
        return []
    scope=report.get('scope',{})
    if not isinstance(scope,dict) or not isinstance(result.get('workflow_failures'),list):
        return []
    from publication_schedule import publication_ids
    try:
        allowed=set(publication_ids(scope['weeks'],scope['per_week']))
    except (KeyError,ValueError,TypeError):
        return []
    records=[]
    for item in result.get('workflow_failures',[]):
        if not isinstance(item,dict) or item.get('task_signature')!=signature:
            continue
        stage=item.get('stage');ident=item.get('slot');artifact=item.get('artifact')
        if stage not in ('plan','publication') or ident not in allowed or not isinstance(artifact,dict):
            continue
        try:
            digest=artifact_hash(artifact)
        except (TypeError,ValueError):
            continue
        if item.get('artifact_sha256')!=digest or not str(item.get('reason') or '').strip():
            continue
        if stage=='plan':
            if item.get('operation')!='revise_plan' or f"{artifact.get('week')}/{artifact.get('day')}"!=ident:
                continue
            if report.get('plan_preflight',{}).get('reviewer_available') is not True:
                continue
        elif (item.get('operation')!='revise_publication' or not isinstance(artifact.get('post'),dict)
                or artifact['post'].get('id')!=ident):
            continue
        records.append(copy.deepcopy(item))
    return records


def bind_recovery_report(result):
    records=verified_failures(result)
    if not records:
        return result
    out=copy.deepcopy(result)
    issues=[]
    for item in records:
        artifact=item['artifact']
        quote=(artifact.get('topic') if item['stage']=='plan' else
               artifact.get('post',{}).get('caption'))
        issues.append({'document':'calendar','type':'quality',
            'quote':str(quote or ''),'reason':item['reason'],'stage':item['stage'],
            'row_context':{'slot':item['slot']},'artifact_sha256':item['artifact_sha256']})
    out['content_delivery']['independent_editor']={'status':'BLOCKED','review_state':'rejected',
        'stage':'production','issues':issues,'repair_slots':sorted({i['slot'] for i in records}),
        'repair_issues_by_slot':{item['slot']:[issue] for item,issue in zip(records,issues)}}
    return out


def retain_rejected_draft(previous, candidate, attempt):
    """Keep the last complete rejected draft if recovery loses checked rows.

    The prior rejection remains authoritative. Failed replacement artifacts
    and diagnostics are retained separately; nothing here approves a draft.
    Returns (result, retained). Missing or unrelated identities cannot replace
    or restore another task's artifacts.
    """
    if not isinstance(previous,dict) or not isinstance(candidate,dict):
        return candidate,False
    signature=previous.get('task_signature')
    if not isinstance(signature,str) or not re.fullmatch(r'[0-9a-f]{64}',signature):
        return candidate,False
    if candidate.get('task_signature')!=signature:
        return candidate,False
    before=previous.get('content_delivery');after=candidate.get('content_delivery')
    if not isinstance(before,dict) or not isinstance(after,dict):return candidate,False
    review=before.get('independent_editor')
    if not isinstance(review,dict) or review.get('status')!='BLOCKED':return candidate,False
    old_count=before.get('checked_posts');new_count=after.get('checked_posts')
    if (type(old_count) is not int or type(new_count) is not int
            or new_count<0 or old_count<=new_count):return candidate,False
    # A complete authoritative success is never replaced by stale content.
    new_review=after.get('independent_editor')
    if (isinstance(new_review,dict) and new_review.get('status')=='PASS'
            and not after.get('unfulfilled')):return candidate,False
    out=copy.deepcopy(previous)
    audit={'attempt':attempt,'reason':'replacement_lost_checked_publications',
        'retained_checked_posts':old_count,'replacement_checked_posts':new_count,
        'replacement':{key:copy.deepcopy(candidate[key]) for key in
            ('task_signature','documents','content_delivery','workflow_failures','recovery_context',
             'error','failure_code','generation_roles') if key in candidate}}
    out.setdefault('failed_repair_attempts',[]).append(audit)
    out['content_delivery']['failed_repair_attempts']=copy.deepcopy(out['failed_repair_attempts'])
    return out,True
