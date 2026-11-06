"""Host-owned classification of independent reviewer outcomes.

Availability is not an editorial finding: a failed or incomplete reviewer may be
replaced by an independent qualified reviewer, but must never approve content.
Keep this contract shared by strategy and final-document review paths.
"""


def review_unavailable(verdict):
    """Single fail-closed contract shared by every editorial boundary.

    Incomplete, structurally contradictory, or unknown model answers must not
    be represented as a completed independent review.
    """
    if not isinstance(verdict, dict):
        return True
    if 'review_state' in verdict and verdict['review_state'] != 'complete':
        return True
    if verdict.get('review_transport_unavailable') or verdict.get('review_control_unavailable'):
        return True
    if verdict.get('review_errors'):
        return True
    if type(verdict.get('pass')) is not bool or not isinstance(verdict.get('issues'), list):
        return True
    if verdict['pass'] and verdict['issues']:
        return True
    substance = verdict.get('substance_review')
    if verdict['pass'] and isinstance(substance, dict) and (
            substance.get('complete') is not True or substance.get('pass') is not True):
        return True
    return False


def unavailable_verdict(reason, findings=None):
    """Represent infrastructure failure without claiming a content defect."""
    issues=[dict(x) for x in (findings or []) if isinstance(x, dict)]
    issues.append({'document':'review', 'type':'review_unavailable',
                   'quote':'', 'adjudication':'unavailable', 'reason':str(reason)[:400]})
    return {'pass':False,'review_state':'unavailable','issues':issues,
            'review_transport_unavailable':True}


def quarantine_unverified_findings(verdict):
    """Outage findings lack repair authority unless independently confirmed.

    Preserve source findings and already validated evidence, but mark other
    allegations as unavailable. The author must not repair unconfirmed claims.
    """
    if not review_unavailable(verdict) or not isinstance(verdict,dict):
        return verdict
    import copy
    out=copy.deepcopy(verdict)
    normalized=[]
    for item in out.get('issues',[]):
        if not isinstance(item,dict):continue
        proof=item.get('finding_validation')
        confirmed=isinstance(proof,dict) and proof.get('decision')=='confirmed'
        source=(item.get('check')=='factual_grounding' or item.get('type') in
                ('invented_fact','unsafe_claim'))
        if not (confirmed or source or item.get('type')=='review_unavailable'):
            item['adjudication']='unavailable'
        normalized.append(item)
    out['issues']=normalized
    out['pass']=False
    out['review_state']='unavailable'
    # Preserve the host marker ONLY for independently grounded findings, e.g.
    # an already verified unsupported factual claim. An unavailable stylistic
    # opinion without validated evidence cannot gain repair authority.
    has_proof=any(item.get('adjudication') not in ('unavailable','invalid_decision') and (
        item.get('type') in ('invented_fact','unsafe_claim') or
        isinstance(item.get('finding_validation'),dict) and
        item['finding_validation'].get('decision')=='confirmed')
        for item in normalized)
    return type(verdict)(out) if has_proof else dict(out)
