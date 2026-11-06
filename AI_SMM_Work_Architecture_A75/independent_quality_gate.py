"""Final production quality boundary before GUI commits a compound delivery.

A completed post count never implies that business claims, formats and the
whole set of documents are semantically consistent. This gate is fail-closed.
Never mutate accepted post checkpoints; report problems for explicit revision.
"""
from __future__ import annotations
from copy import deepcopy
import json
from independent_acceptance_editor import parse_verdict, ValidatedEditorialVerdict
from editorial_evidence_matrix import publication_rows
from review_arbitration import is_source_finding

from compound_retry import _verified_output
from reviewer_outcomes import review_unavailable


def bind_stage_rejection(result, findings):
    """Retain exact upstream review evidence even before dependent outputs exist.

    A missing calendar must not erase the strategy verdict or make its rejected
    draft unreachable by the same bounded correction used after final review.
    This report never certifies completion.
    """
    if not isinstance(result, dict) or result.get('failure_code') == 'independent_review_unavailable':
        return result
    documents = {d.get('kind'): d for d in result.get('documents', []) if isinstance(d, dict)}
    rejected = documents.get('strategy', {})
    if rejected.get('valid') is not False or not isinstance(rejected.get('content'), str):
        return result
    grounded = []
    for issue in findings:
        if (isinstance(issue, dict) and issue.get('document') == 'strategy'
                and isinstance(issue.get('quote'), str) and issue['quote'].strip()
                and issue['quote'] in rejected['content']
                and isinstance(issue.get('reason'), str) and issue['reason'].strip()
                and issue.get('adjudication') not in ('unavailable', 'invalid_decision')):
            grounded.append(deepcopy(issue))
    if not grounded:
        return result
    out = deepcopy(result)
    report = out.setdefault('content_delivery', {})
    report.update(deliverables={'strategy': 'blocked', 'calendar': 'pending'},
                  unfulfilled=[str(rejected.get('reason') or 'Стратегия отклонена')],
                  rendered_media='not_requested')
    report['independent_editor'] = {'status': 'BLOCKED', 'review_state': 'rejected',
        'stage': 'strategy', 'issues': grounded, 'repair_slots': [], 'repair_issues_by_slot': {}}
    return out


def _calendar_slots_for_issues(issues, calendar):
    """Map grounded calendar findings back to exact, still-unapproved slots."""
    rows=publication_rows(calendar)
    by_slot={row.get('slot'):row for row in rows if row.get('slot')}
    slots=set()
    for issue in issues:
        if not isinstance(issue,dict) or issue.get('document')!='calendar':
            continue
        context=issue.get('row_context')
        if isinstance(context,dict):
            slot=context.get('slot')
            if isinstance(slot,str) and slot in by_slot:
                slots.add(slot)
                continue
        quote=str(issue.get('quote') or '').strip()
        if not quote:
            continue
        matches=[row for row in rows if quote in row['raw'] or row['raw'] in quote]
        if len(matches)==1:
            slots.add(matches[0]['slot'])
    return sorted(slots)


def _calendar_issues_by_slot(issues, calendar):
    rows=publication_rows(calendar)
    by_slot={row.get('slot'):row for row in rows if row.get('slot')}
    result={}
    for issue in issues:
        if not isinstance(issue,dict) or issue.get('document')!='calendar':
            continue
        context=issue.get('row_context')
        if isinstance(context,dict):
            slot=context.get('slot')
            if isinstance(slot,str) and slot in by_slot:
                result.setdefault(slot,[]).append(issue)
                continue
        quote=str(issue.get('quote') or '').strip()
        matches=[row for row in rows if quote and (quote in row['raw'] or row['raw'] in quote)]
        if len(matches)==1:
            result.setdefault(matches[0]['slot'],[]).append(issue)
    return result


def _confirmed_for_partial_repair(item):
    return (isinstance(item,dict) and item.get('document') in ('strategy','calendar')
            and item.get('type')!='review_unavailable'
            and item.get('adjudication') not in ('unavailable','invalid_decision')
            and (is_source_finding(item) or
                 isinstance(item.get('finding_validation'),dict)
                 and item['finding_validation'].get('decision')=='confirmed'))


def _applicability_metadata(verdict):
    return {key:deepcopy(verdict[key]) for key in ('finding_adjudication',
        'full_recheck_after_finding_dismissal','applicability_review_complete','substance_review','independent_reader')
        if isinstance(verdict,dict) and key in verdict}


def final_quality_gate(result, request, reviewer):
    """Require an independent, evidence-grounded verdict for an otherwise ready task.

    A failure does not erase a strategy or existing publication checkpoints. It
    prevents the calendar from being persisted as an approved final result.
    """
    if not _verified_output(result):
        return result
    docs=result['documents']
    by_kind={item['kind']:item for item in docs if isinstance(item,dict) and item.get('kind') in ('strategy','calendar')}
    reason=''
    verdict=None
    disproved=[]
    review_error=None
    partial_review_unavailable=False
    validation_diagnostics=[]
    # A malformed/partial independent review is a review-system failure, not a
    # defect in the generated documents. Retry the review once against the
    # identical immutable inputs. Never rewrite content to compensate for an
    # unavailable validator.
    for review_attempt in range(2):
        try:
            raw_verdict=reviewer(str(request),by_kind['strategy']['content'],by_kind['calendar']['content'])
            disproved=(raw_verdict.get('disproved_absence_claims', [])
                       if isinstance(raw_verdict,dict) else [])
            if isinstance(raw_verdict,dict) and raw_verdict.get('review_state')=='unavailable':
                review_issues=raw_verdict.get('issues',[]) if isinstance(raw_verdict.get('issues'),list) else []
                validation_diagnostics=[item for item in review_issues if isinstance(item,dict)
                    and (item.get('type')=='review_unavailable'
                         or item.get('adjudication') in ('unavailable','invalid_decision')
                         or not _confirmed_for_partial_repair(item))]
                # A partial outage does not erase separately verified findings.
                # Keep those exact, actionable issues so the production layer
                # can repair only unapproved document fields. The unavailable
                # evidence stays visible and continues to block final approval.
                actionable=[item for item in review_issues if _confirmed_for_partial_repair(item)]
                if actionable:
                    if not isinstance(raw_verdict,ValidatedEditorialVerdict):
                        checked=parse_verdict(json.dumps({'pass':False,'issues':actionable},ensure_ascii=False),
                            strategy=by_kind['strategy']['content'],calendar=by_kind['calendar']['content'])
                        actionable=checked['issues']
                    verdict=ValidatedEditorialVerdict({'pass':False,'issues':actionable,
                        'disproved_absence_claims':disproved,
                        'review_state':'complete',**_applicability_metadata(raw_verdict)})
                    partial_review_unavailable=True
                    review_error=None
                    break
                if isinstance(raw_verdict.get('independent_reader'),dict):
                    from review_arbitration import FindingReviewUnavailable
                    raise FindingReviewUnavailable('Независимое содержательное чтение не завершено',review_issues)
                if raw_verdict.get('review_transport_unavailable'):
                    from review_arbitration import FindingReviewUnavailable
                    raise FindingReviewUnavailable('Независимая модель недоступна; незавершённые окна отложены',review_issues)
                if raw_verdict.get('review_control_unavailable'):
                    from review_arbitration import FindingReviewUnavailable
                    raise FindingReviewUnavailable('Редактор не прошёл контроль содержательности; проверка отложена',review_issues)
                if raw_verdict.get('applicability_review_complete'):
                    from review_arbitration import FindingReviewUnavailable
                    raise FindingReviewUnavailable('Применимость редакторских замечаний не установлена',review_issues)
                raise ValueError('Источник-арбитр не завершил проверку некоторых утверждений')
            # Model-supplied state must be checked before parse_verdict
            # canonicalizes the payload. Otherwise unknown states can be
            # silently replaced with "complete" and granted approval.
            if (isinstance(raw_verdict, dict)
                    and 'review_state' in raw_verdict
                    and raw_verdict['review_state'] != 'complete'):
                raise ValueError('Итоговый редактор вернул неподдерживаемое состояние проверки')
            if isinstance(raw_verdict,ValidatedEditorialVerdict):
                # `call_editor` already validated every evidence ID and
                # canonicalized each claim from the shared immutable catalog.
                # Re-parsing it here rebuilt evidence in legacy mode and
                # compared whole-document quote spans to individual segments.
                verdict=raw_verdict
            else:
                verdict=parse_verdict(json.dumps(raw_verdict,ensure_ascii=False),
                                      strategy=by_kind['strategy']['content'],
                                      calendar=by_kind['calendar']['content'])
            if (not isinstance(verdict,dict) or type(verdict.get('pass')) is not bool
                    or not isinstance(verdict.get('issues'),list)):
                raise ValueError('Итоговый независимый редактор вернул неверный формат')
            if verdict['pass'] and review_unavailable(verdict):
                raise ValueError('Итоговый редактор не прошёл единый контракт достоверности')
            if verdict['pass'] and verdict['issues']:
                raise ValueError('Итоговый независимый редактор вернул противоречивый ответ')
            if verdict['pass'] and isinstance(verdict.get('substance_review'),dict) and (
                    verdict['substance_review'].get('complete') is not True
                    or verdict['substance_review'].get('pass') is not True):
                raise ValueError('Содержательные критерии не подтверждают итоговое одобрение')
            review_error=None
            break
        except Exception as exc:
            review_error=exc
            verdict=None
            if not getattr(exc,'retryable',True):
                break
    if review_error is not None:
        review_context='после повторного вызова' if review_attempt else 'после одного вызова'
        reason=(f'Независимая проверка не дала достоверного результата {review_context}: '
                +type(review_error).__name__+': '+str(review_error)[:400])
    elif verdict['pass']:
        out=deepcopy(result)
        out['content_delivery']['independent_editor']={'status':'PASS','review_state':'complete',
            'issues':[],'disproved_absence_claims':disproved[:12] if isinstance(disproved,list) else [],
            **_applicability_metadata(verdict)}
        return out
    else:
        issues=verdict['issues']
        if not issues:
            reason='Итоговый независимый редактор отклонил работу без доказательств'
        else:
            reason='; '.join(str(i.get('reason') or 'Редакторская проблема')[:240]
                            for i in issues[:5] if isinstance(i,dict))
        if partial_review_unavailable:
            reason += ' Полный аудит частично недоступен; исправляются только отдельно подтверждённые замечания.'
    out=deepcopy(result)
    target=next(doc for doc in out['documents'] if doc.get('kind')=='calendar')
    target['valid']=False
    target['reason']='Итоговая независимая проверка не пройдена: '+(reason or 'нет подтверждения')
    report=out['content_delivery']
    if not isinstance(report.get('unfulfilled'),list):
        report['unfulfilled']=[]
    report['unfulfilled'].append(target['reason'])
    report['deliverables']['calendar']='blocked'
    affected={i.get('document') for i in (verdict or {}).get('issues',[]) if isinstance(i,dict)}
    if verdict is None:
        affected.add('strategy')
    if 'strategy' in affected:
        for doc in out['documents']:
            if doc.get('kind')=='strategy':
                doc['valid']=False
                doc['reason']=target['reason']
        report['deliverables']['strategy']='blocked'

    if isinstance(report.get('task_orchestration'),dict):
        report['task_orchestration']['completed']=False
    report['independent_editor']={'status':'BLOCKED',
        'review_state':'unavailable' if (review_error is not None or partial_review_unavailable) else 'rejected',
        'issues':deepcopy(verdict['issues']) if isinstance(verdict,dict) and isinstance(verdict.get('issues'),list) else [],
        'validation_diagnostics':validation_diagnostics[:12],
        'reason':reason[:900],
        **_applicability_metadata(verdict or (raw_verdict if 'raw_verdict' in locals() else None)),
        'disproved_absence_claims':(disproved[:12] if 'disproved' in locals() and isinstance(disproved,list) else [])}
    report['independent_editor']['repair_slots']=_calendar_slots_for_issues(
        report['independent_editor']['issues'],by_kind.get('calendar',{}).get('content',''))
    report['independent_editor']['repair_issues_by_slot']=_calendar_issues_by_slot(
        report['independent_editor']['issues'],by_kind.get('calendar',{}).get('content',''))
    return out
