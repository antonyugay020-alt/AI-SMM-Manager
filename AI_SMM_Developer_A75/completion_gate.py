"""GUI/backend boundary: a generated draft is not a persisted finished task.

No industry-specific prompts, external state or model calls. The UI supplies
which documents were actually reread from storage after saving.
"""

def standalone_review_matches(review, request, draft):
    """A PASS label cannot certify different text or incomplete review work."""
    if (not isinstance(review,dict) or review.get('status')!='PASS' or review.get('pass') is not True
            or review.get('issues')!=[] or review.get('review_errors')!={}
            or review.get('review_state')!='complete' or review.get('independent_model') is not True
            or not isinstance(request,str) or not request.strip()
            or not isinstance(draft,str) or not draft.strip()):return False
    from artifact_review import standalone_substance_contract,review_source_signature
    from generic_text_review import CHECKS,validate_model_assignments
    checks=review.get('checks');completed=review.get('completed_dimensions')
    if (not isinstance(checks,dict) or set(checks)!=set(CHECKS)
            or any(value is not True for value in checks.values())
            or not isinstance(completed,list) or not all(isinstance(key,str) for key in completed)
            or len(completed)!=len(CHECKS) or set(completed)!=set(CHECKS)):
        return False
    author=review.get('author_model')
    if not isinstance(author,str) or not author.strip():return False
    try:
        validate_model_assignments(author,review.get('reviewer_models'))
        return (review.get('source_signature')==review_source_signature(request,{'standalone':draft})
            and standalone_substance_contract(request,draft.strip()).matches_proof(review.get('substance_review')))
    except (TypeError,ValueError,KeyError):return False

def calendar_matches_parameters(calendar, expected_parameters):
    """Count canonical output rows/fields, never infer intent from user words."""
    if not isinstance(calendar,str) or not isinstance(expected_parameters,dict):return False
    from editorial_evidence_matrix import publication_rows, _cells
    from publication_schema import CALENDAR_COLUMNS, publication_field_type
    import re
    schedule=expected_parameters.get('schedule')
    if (not isinstance(schedule,dict) or any(type(schedule.get(key)) is not int
            for key in ('weeks','per_week','total'))):return False
    weeks,cadence,total=(schedule[key] for key in ('weeks','per_week','total'))
    if not 1<=weeks<=26 or not 1<=cadence<=7 or total!=weeks*cadence:return False
    rows=publication_rows(calendar)
    if len(rows)!=total:return False
    # A malformed or extra table row must not disappear from the count merely
    # because the evidence parser could not recognize its schema or slot.
    data=[]
    for line in calendar.splitlines():
        if not line.lstrip().startswith('|'):continue
        cells=_cells(line)
        if cells and (publication_field_type(cells[0])=='slot'
                or all(re.fullmatch(r'[-: ]+',cell or '-') for cell in cells)):continue
        data.append(line)
    if data!=[row['raw'] for row in rows]:return False
    required={key for _,key in CALENDAR_COLUMNS};slots=set();counts={week:0 for week in range(1,weeks+1)}
    for row in rows:
        fields=row['typed_fields']
        if (len(row['fields'])!=len(required) or set(fields)!=required
                or any(not value.strip() for value in fields.values())):return False
        week,day=row['slot'].split('/',1);ident=(int(week),day.strip().casefold())
        if ident in slots or ident[0] not in counts:return False
        slots.add(ident);counts[ident[0]]+=1
    return all(value==cadence for value in counts.values())


def bundle_review_matches(editor, request, artifacts):
    """Receive both opinions against the exact task and canonical artifacts."""
    if (not accepted_substantive_review(editor) or not isinstance(request,str)
            or not isinstance(artifacts,dict) or set(artifacts)!={'strategy','calendar'}
            or any(not isinstance(body,str) or not body.strip() for body in artifacts.values())):
        return False
    from artifact_review import document_substance_contract, review_source_signature
    from independent_acceptance_editor import _inventory_segments, substance_reader_contract
    strategy,calendar=artifacts['strategy'].strip(),artifacts['calendar'].strip()
    try:
        full=document_substance_contract(request,_inventory_segments(strategy,calendar))
        _,reader=substance_reader_contract(request,strategy,calendar)
        source=review_source_signature(request,artifacts)
        primary_proof=editor['substance_review'];reader_proof=editor['independent_reader']['substance_review']
        return (primary_proof.get('source_signature')==source
            and reader_proof.get('source_signature')==source
            and full.matches_proof(primary_proof) and reader.matches_proof(reader_proof))
    except (TypeError,ValueError,KeyError):return False

def delivery_matches_parameters(delivery, expected_parameters):
    """Compare the executor's declared scope with host-owned typed parameters."""
    if not isinstance(delivery,dict) or not isinstance(expected_parameters,dict):return False
    try:
        from task_semantics import semantic_parameters
        expected=semantic_parameters({'semantic_authorized':True,
            'semantic_execution_kind':expected_parameters.get('kind'),
            'semantic_schedule':expected_parameters.get('schedule'),
            'semantic_requested_media':expected_parameters.get('requested_media')})
    except (TypeError,ValueError):return False
    if expected['kind']!='bundle':return False
    actual=delivery.get('scope');schedule=expected['schedule']
    return (isinstance(actual,dict) and all(type(actual.get(key)) is int for key in ('weeks','per_week','planned_total'))
        and (actual['weeks'],actual['per_week'],actual['planned_total'])==
            (schedule['weeks'],schedule['per_week'],schedule['total']))


def _complete_substance_proof(proof):
    if not isinstance(proof,dict) or proof.get('policy')!='substantive_delivery_r82':return False
    expected=proof.get('expected_checks');assessments=proof.get('assessments')
    return (proof.get('complete') is True and proof.get('pass') is True
        and isinstance(expected,list) and bool(expected)
        and all(isinstance(key,str) and bool(key.strip()) for key in expected)
        and len(set(expected))==len(expected)
        and isinstance(assessments,dict) and set(expected)==set(assessments)
        and all(isinstance(row,dict) and row.get('status')=='satisfied' for row in assessments.values())
        and isinstance(proof.get('controls'),dict) and proof['controls'].get('passed') is True)


def accepted_substantive_review(editor):
    """Evidence required before a semantic bundle calendar becomes accepted."""
    if (not isinstance(editor,dict) or editor.get('status')!='PASS' or editor.get('issues')!=[]
            or editor.get('review_errors') or editor.get('review_state') in ('partial','unavailable','rejected')
            or not _complete_substance_proof(editor.get('substance_review'))):return False
    reader=editor.get('independent_reader')
    if (not isinstance(reader,dict) or reader.get('status')!='PASS'
            or reader.get('independence_verified') is not True
            or not _complete_substance_proof(reader.get('substance_review'))):return False
    from model_roles import model_family
    actual=reader.get('model');primary=reader.get('independent_from')
    return (isinstance(actual,str) and bool(actual.strip()) and isinstance(primary,str)
            and bool(primary.strip()) and model_family(actual)!=model_family(primary))


def can_close_task(verified_kinds, delivery, *, require_editor=True, expected_parameters=None,
                   request=None, artifacts=None, execution_contract=None):
    """Fail closed on missing/inconsistent delivery proof or saved documents."""
    if not isinstance(delivery, dict):
        return False
    # When a live task identity is available from SQLite, accept only a result
    # claiming that exact task, request and immutable output scope.
    if execution_contract is not None:
        from workflow_authority import ExecutionContract
        if (not isinstance(execution_contract, ExecutionContract)
                or not execution_contract.matches(delivery.get('execution_contract'))
                or delivery.get('task_id') != execution_contract.task_id
                or delivery.get('request_sha256') != execution_contract.request_sha256):
            return False
        if request is None:
            return False
        import hashlib
        if hashlib.sha256(request.encode('utf-8')).hexdigest() != execution_contract.request_sha256:
            return False
        from workflow_authority import verify_ready_record
        if not verify_ready_record(delivery.get('workflow_assessment'),
                                   execution_contract.required_total):
            return False
    # Modern deliveries must supply an explicit host-owned stage assessment.
    if 'workflow_assessment' in delivery:
        from workflow_authority import verify_ready_record
        scope = delivery.get('scope')
        if not isinstance(scope, dict) or not verify_ready_record(
                delivery['workflow_assessment'], scope.get('planned_total')):
            return False
    if not isinstance(verified_kinds, (set, frozenset, list, tuple)):
        return False
    # The production UI must not close a task whose whole-document semantic
    # audit was skipped, malformed, or blocked. Backend recovery uses
    # require_editor=False only to decide whether to retry generation.
    if require_editor and not (isinstance(delivery.get('independent_editor'),dict)
                               and delivery['independent_editor'].get('status')=='PASS'
                               and delivery['independent_editor'].get('issues') == []):
        return False
    proof=(delivery.get('independent_editor') or {}).get('substance_review')
    reader=(delivery.get('independent_editor') or {}).get('independent_reader')
    editor=delivery.get('independent_editor') or {}
    if require_editor and (editor.get('review_errors') or editor.get('review_state') in ('partial','unavailable','rejected')):
        return False
    if expected_parameters is not None:
        if not delivery_matches_parameters(delivery,expected_parameters):return False
        # This executor creates text/scenarios, not verified rendered media.
        if expected_parameters.get('requested_media') is not False:return False
        if require_editor and not accepted_substantive_review(editor):return False
        if require_editor and (not isinstance(artifacts,dict)
                or not calendar_matches_parameters(artifacts.get('calendar'),expected_parameters)
                or not bundle_review_matches(editor,request,artifacts)):return False
    if reader is not None and (not isinstance(reader,dict) or reader.get('status')!='PASS'):
        return False
    if isinstance(reader,dict):
        from model_roles import model_family
        actual=reader.get('model');primary=reader.get('independent_from')
        if (not isinstance(actual,str) or not actual.strip()
                or not isinstance(primary,str) or not primary.strip()
                or model_family(actual)==model_family(primary)):
            return False
        reading=reader.get('substance_review')
        if (not isinstance(reading,dict) or reading.get('complete') is not True or reading.get('pass') is not True
                or not isinstance(reading.get('controls'),dict) or reading['controls'].get('passed') is not True):
            return False
    if proof is not None:
        if (not isinstance(proof,dict) or proof.get('complete') is not True or proof.get('pass') is not True
                or not isinstance(proof.get('expected_checks'),list) or not proof['expected_checks']
                or not isinstance(proof.get('assessments'),dict)
                or set(proof['expected_checks'])!=set(proof['assessments'])
                or any(not isinstance(row,dict) or row.get('status')!='satisfied'
                       for row in proof['assessments'].values())):
            return False
        if proof.get('policy')=='substantive_delivery_r82' and not (
                isinstance(proof.get('controls'),dict) and proof['controls'].get('passed') is True):
            return False
    if not {'strategy', 'calendar'}.issubset(set(verified_kinds)):
        return False
    if delivery.get('unfulfilled') != []:
        return False
    # This document-only pipeline has no verified media asset receipts. It
    # cannot certify generated media from an untrusted status label.
    if delivery.get('rendered_media') != 'not_requested':
        return False
    if delivery.get('deliverables') != {'strategy': 'validated', 'calendar': 'validated'}:
        return False
    scope = delivery.get('scope')
    if not isinstance(scope, dict):
        return False
    weeks, cadence, total = scope.get('weeks'), scope.get('per_week'), scope.get('planned_total')
    if (any(type(value) is not int for value in (weeks, cadence, total))
            or not 1<=weeks<=26 or not 1<=cadence<=7 or total != weeks * cadence):
        return False
    if type(delivery.get('checked_posts')) is not int or delivery['checked_posts'] != total:
        return False
    fields = delivery.get('content_components_checked')
    if not isinstance(fields, dict) or any(type(fields.get(name)) is not int or fields.get(name) != total
            for name in ('caption', 'visual', 'cta', 'kpi', 'audience', 'measurement', 'materials')):
        return False
    plan = delivery.get('plan_preflight')
    if not isinstance(plan,dict) or plan.get('reviewer_available') is not True or plan.get('unresolved_slots') != []:
        return False
    orchestration = delivery.get('task_orchestration')
    if not isinstance(orchestration,dict) or orchestration.get('completed') is not True:
        return False
    return True
