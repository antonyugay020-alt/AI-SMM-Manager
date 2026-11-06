"""Model-independent IDs and global plan governance before production.

Audit an entire task plan against its ORIGINAL request; do not use industry rules.
An unavailable reviewer is reported, never misrepresented as an approval.
An explicit rejection blocks only unaccepted slots; stored deliverables survive.
"""
import json
import inspect
import re
from semantic_brief import propose_topic_replacement, topic_repeats
from deliverable_contract import planning_request_contract
from task_semantics import ai_content_review_active


# This reviewer accepts a *plan*, not the downstream publication artifacts.
# Keeping the contract explicit prevents full-delivery requirements (caption,
# CTA, finished visual, measurement details) from being evaluated as if they
# were fields that must already exist in a topic plan.
PLAN_REVIEW_FIELDS = ('topic', 'brief', 'goal', 'audience', 'format', 'platform')
DOWNSTREAM_PUBLICATION_FIELDS = ('caption', 'cta', 'visual', 'kpi', 'measurement', 'materials')


def slot_id(slot):
    return f"{slot['week']}/{slot['day']}"


def structural_issues(slots, required_ids):
    """Fail closed on malformed, missing, duplicate and unrequested plan entries.

    All required deliverables are explicit. An extra slot is a *global* plan
    corruption, never an optional post to be silently generated or ignored.
    """
    expected = list(required_ids)
    if not isinstance(slots, list) or not expected or len(set(expected)) != len(expected):
        return {ident: 'Некорректный состав обязательных слотов' for ident in expected}
    issues = {}
    counts = {}
    unexpected = False
    topics = {}
    mandatory = ('topic', 'brief', 'goal', 'audience', 'format', 'platform')
    for slot in slots:
        if not isinstance(slot, dict):
            unexpected = True
            continue
        week = slot.get('week')
        day = slot.get('day')
        if type(week) is not int or not isinstance(day, str) or not day.strip():
            unexpected = True
            continue
        ident = f'{week}/{day}'
        if ident not in expected:
            unexpected = True
            continue
        counts[ident] = counts.get(ident, 0) + 1
        for field in mandatory:
            if not isinstance(slot.get(field), str) or not slot[field].strip():
                issues[ident] = 'Неверная или незаполненная структура плана: ' + field
                break
        if str(slot.get('platform','')).casefold()=='telegram' and str(slot.get('format','')).casefold()=='reels':
            issues[ident]='Формат Reels не соответствует выбранной площадке Telegram'
        if isinstance(slot.get('topic'),str):
            normalized = ' '.join(slot['topic'].casefold().split())
            if normalized in topics:
                issues[ident] = 'План содержит точное повторение темы'
                issues[topics[normalized]] = 'План содержит точное повторение темы'
            else:
                topics[normalized] = ident
    for ident in expected:
        if counts.get(ident,0) != 1:
            issues[ident] = 'План содержит отсутствующий или дублированный слот'
    if unexpected:
        for ident in expected:
            issues[ident] = 'В плане присутствуют лишние или некорректные слоты'
    return issues


def parse_review(raw, ids, allowed_dimensions=PLAN_REVIEW_FIELDS):
    try:
        obj=json.loads(str(raw))
    except (TypeError, ValueError):
        return None
    if not isinstance(obj,dict) or set(obj) != {'issues'} or not isinstance(obj.get('issues'),list):
        # Extra contradictory approval/failure flags cannot silently override
        # an empty issues list. The reviewer protocol is a strict JSON schema.
        return None
    problems={}
    allowed_dimensions=set(allowed_dimensions)
    for entry in obj['issues']:
        if not isinstance(entry,dict): return None
        ident=entry.get('id')
        dimension=entry.get('dimension')
        reason=entry.get('reason')
        if (set(entry) != {'id','dimension','reason'} or not isinstance(ident,str)
                or ident not in ids or ident in problems
                or not isinstance(dimension,str) or dimension not in allowed_dimensions
                or not isinstance(reason,str) or not reason.strip()):
            return None  # Unknown targets and malformed issues are not implicit approval.
        problems[ident]={'dimension':dimension,'reason':reason.strip()[:400]}
    return problems


_PLAN_COUNT_OBJECTION = re.compile(
    r"\b(?:план|в\s+плане|частот\w*|количеств\w*)\b.{0,180}\b"
    r"(?:только|всего|не\s+хватает|меньше|больше|не\s+соответствует|"
    r"не\s+совпадает|должн\w*|нужно)\b.{0,100}\b"
    r"(?:публикац\w*|пост\w*|слот\w*|в\s+недел\w*)\b|"
    r"\b(?:публикац\w*|пост\w*|слот\w*|в\s+недел\w*)\b.{0,160}\b"
    r"(?:не\s+соответствует|не\s+совпадает|не\s+хватает|только|всего)\b",
    re.I)


def govern_plan(ask, model, request, brief, strategy, slots, accepted_ids, *,
                expected_ids, review_model=None, on_event=lambda *args,**kwargs:None,
                allow_replan=True, repair_targets=None, max_candidates=None):
    """Review whole plan, then re-plan only rejected *unaccepted* slots.

    Returns (new_slots, unresolved, review_available). Candidate reviews are
    independent of the topic proposer, and successful replacements are returned
    to the caller for atomic checkpointing before post generation.
    """
    slots=[dict(s) if isinstance(s,dict) else s for s in slots] if isinstance(slots,list) else []
    configured_reviewers=review_model or model
    reviewers=(list(dict.fromkeys(str(x) for x in configured_reviewers if str(x).strip()))
               if isinstance(configured_reviewers,(list,tuple)) else [configured_reviewers])
    if not reviewers:
        reviewers=[model]
    original_slots=[dict(s) if isinstance(s,dict) else s for s in slots]
    structural=structural_issues(slots,expected_ids)
    pending_ids=set(expected_ids)-set(accepted_ids)
    # A malformed plan is not semantically re-plannable: its canonical IDs or
    # field contract must first be repaired by the planner. Never submit it
    # to a content writer, or treat a mock audit as authorization.
    if structural:
        blocked=dict(structural)
        on_event('plan_structure_rejected', issues=len(blocked))
        return slots,blocked,False
    by_id={slot_id(s):s for s in slots}
    system=(
      'Ты НЕЗАВИСИМЫЙ аудитор документа типа content_plan. '
      'Это проверка только плана тем и слотов, а не всего пакета готовых материалов. '
      'Для каждого замечания обязательно выбери dimension из разрешённых полей контракта. '
      'Проверяй слот как единое целое: тема задаёт конкретный предмет, а brief и goal объясняют, '
      'как этот предмет помогает исходному результату с учётом аудитории, формата и площадки. '
      'Сверяй весь слот с исходным запросом, проверенными бизнес-фактами и ограничениями. '
      'Не требуй, чтобы одна только формулировка темы повторяла финальную цель или CTA. '
      'Отмечай только ЯВНЫЕ нарушения; не считай допустимый тематический угол несоответствием '
      'только потому, что он раскрывает цель через полезный промежуточный вопрос или пример. '
      'Не требуй точных свойств товара/услуги, '
      'если их нет в источниках и тема может корректно работать как нейтральный критерий выбора или вопрос аудитории. '
      'Не отклоняй тему лишь потому, что в ней нет неподтверждённого деталями примера; отклоняй только если '
      'она уводит от заявленной цели или выдаёт предположение за факт бизнеса. '
      'не меняй план ради стиля. Не расширяй бизнес на соседние направления. '
      'Не требуй в плане готовый текст публикации, CTA, готовый визуал, KPI/метод измерения '
      'или материалы: это отдельные downstream-артефакты, которые создаются и проверяются '
      'на следующих этапах. Не считай отсутствие этих полей в плане нарушением. '
      'Верни СТРОГО JSON {"issues":[{"id":"неделя/день","dimension":"topic|brief|goal|audience|format|platform","reason":"..."}]}. '
      'Если нарушений нет, верни {"issues":[]}. Отвечай только по переданным слотам.'
    )
    payload={'artifact_contract':{
                 'artifact_type':'content_plan',
                 'reviewable_fields':list(PLAN_REVIEW_FIELDS),
                 'downstream_fields_not_reviewed_here':list(DOWNSTREAM_PUBLICATION_FIELDS),
                 'downstream_fields_still_required_by_final_delivery':list(DOWNSTREAM_PUBLICATION_FIELDS)},
             'original_request':request[:10000],
             'request_contract':planning_request_contract(request,len(expected_ids)),
             'business_context':brief,
             'strategy_excerpt':str(strategy)[:2500],
             'plan':[{k:v for k,v in s.items() if k in ('week','day','topic','brief','goal','audience','format','platform')} for s in slots],
             'already_accepted':sorted(accepted_ids)}
    ids=set(by_id)
    def schema_for(allowed_ids, allowed_dimensions=PLAN_REVIEW_FIELDS):
        allowed=set(allowed_ids)
        return {'type':'object','additionalProperties':False,
            'properties':{'issues':{'type':'array','maxItems':len(allowed),
                'items':{'type':'object','additionalProperties':False,
                    'properties':{'id':{'type':'string','enum':sorted(allowed)},
                                  'dimension':{'type':'string','enum':list(allowed_dimensions)},
                                  'reason':{'type':'string','minLength':1,'maxLength':400}},
                    'required':['id','dimension','reason']}}},'required':['issues']}
    active_reviewer=reviewers[0]
    def invoke_review(user_text, allowed_ids=None, reviewer_model=None):
        reviewer_model=reviewer_model or active_reviewer
        backup_reviewer=str(reviewer_model).casefold()!=str(reviewers[0]).casefold()
        try:
            params=inspect.signature(ask).parameters.values()
            params=list(params)
            accepts_kwargs=any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
            names={p.name for p in params}
            supports_schema=accepts_kwargs or 'json_schema' in names
        except (TypeError,ValueError):
            accepts_kwargs=False
            names=set()
            supports_schema=False
        kwargs={}
        if supports_schema:
            kwargs['json_schema']=schema_for(allowed_ids or ids)
        if accepts_kwargs or 'backup_reviewer' in names:
            kwargs['backup_reviewer']=backup_reviewer
        return ask(reviewer_model,system,user_text,**kwargs)
    last_error='Неполный или невалидный ответ независимого аудитора'
    parsed=None
    for reviewer_index,reviewer in enumerate(reviewers):
        try:
            raw=invoke_review(json.dumps(payload,ensure_ascii=False),reviewer_model=reviewer)
            parsed=parse_review(raw,ids)
            if parsed is None:
                on_event('plan_review_retry',reviewer=reviewer,
                         reason='first response did not match review schema')
                correction=('Предыдущий ответ не прошёл проверку структуры. Повтори аудит только content_plan. '
                           'Не проверяй готовые публикации, CTA, визуалы, KPI, измерение или материалы: они относятся '
                           'к следующим артефактам. Верни только JSON вида {"issues":[]}; при замечаниях укажи точный ID, '
                           'dimension только из artifact_contract.reviewable_fields и reason. Не добавляй другие поля, '
                           'Markdown или пояснение вне JSON.\n\n'
                           +json.dumps(payload,ensure_ascii=False))
                parsed=parse_review(invoke_review(correction,ids,reviewer),ids)
            if parsed is not None:
                active_reviewer=reviewer
                break
            last_error='Неполный или невалидный ответ независимого аудитора'
        except Exception as exc:
            parsed=None
            last_error=str(exc)[:200]
            on_event('plan_review_unavailable',reviewer=reviewer,error=last_error)
        if reviewer_index+1<len(reviewers):
            on_event('plan_review_failover',from_model=reviewer,
                     to_model=reviewers[reviewer_index+1],attempt=reviewer_index+1)
    if parsed is None:
        on_event('plan_review_unavailable',error=last_error)
        return slots,{ident:structural.get(ident,'Независимый аудит плана недоступен') for ident in set(expected_ids)},False
    # The exact slot contract is deterministic and was checked above. A model
    # reviewer must not block a structurally complete plan with a contradictory
    # count/frequency claim (R19 Qwen reviewer said 2 posts violated a brief
    # explicitly requesting 2 total). Content and topic objections still pass
    # through the normal independent review/replan path.
    for ident,item in list(parsed.items()):
        reason=item['reason']
        if not ai_content_review_active() and _PLAN_COUNT_OBJECTION.search(reason):
            on_event('plan_review_structural_claim_overruled',publication=ident,
                     reason=reason[:240],verified_slots=len(slots),
                     expected_slots=len(expected_ids))
            parsed.pop(ident)
    # A model's valid but overly narrow topic objection is not enough to force
    # repeated rewrites. When a distinct reviewer family is already assigned,
    # require independent agreement on the same slot and dimension. A split
    # verdict proceeds to publication-level review; it is never counted as an
    # approval of the finished content.
    counter_reviewer=next((name for name in reviewers
                           if str(name).casefold()!=str(active_reviewer).casefold()),None)
    if parsed and counter_reviewer:
        try:
            counter_text=json.dumps({**payload,
                'instruction':'Дай независимое второе мнение по всему плану. '
                    'Проверяй каждый слот целиком: предмет темы вместе с brief и goal. '
                    'Не требуй, чтобы тема сама повторяла итог или CTA. Отмечай только явное '
                    'несоответствие, противоречие подтверждённым фактам или выход за ограничения.'},
                ensure_ascii=False)
            counter=parse_review(invoke_review(counter_text,reviewer_model=counter_reviewer),ids)
            if counter is None:
                retry=('Повтори независимую проверку всего плана и верни только JSON '
                       '{"issues":[]} либо замечания с точными ID и dimension из разрешённой схемы. '
                       'Оценивай тему, brief и goal вместе.\n\n'+counter_text)
                counter=parse_review(invoke_review(retry,ids,counter_reviewer),ids)
            if counter is None:
                raise ValueError('Второй независимый редактор вернул неполный ответ')
        except Exception as exc:
            on_event('plan_review_countercheck_unavailable',reviewer=counter_reviewer,
                     error=str(exc)[:200],
                     action='defer_objection_to_publication_and_final_document_reviews')
            # An unavailable counter-review is neither agreement nor approval.
            # Keep the original plan and defer the unconfirmed objection to the
            # stricter per-publication and final-document gates. Deterministic
            # structural failures above remain blocking.
            parsed={}
        agreed={}
        for ident,first in parsed.items():
            second=counter.get(ident)
            if second and second.get('dimension')==first.get('dimension'):
                agreed[ident]={'dimension':first['dimension'],
                    'reason':(first['reason']+'; подтверждено независимым редактором: '
                              +second['reason'])[:400]}
                on_event('plan_review_rejection_confirmed',publication=ident,
                         dimension=first['dimension'],reviewers=[active_reviewer,counter_reviewer])
            else:
                on_event('plan_review_disagreement',publication=ident,
                         dimension=first['dimension'],reviewers=[active_reviewer,counter_reviewer],
                         action='defer_to_publication_level_review')
        parsed=agreed
    issues={ident:item['reason'] for ident,item in parsed.items()}
    issues.update(structural)
    # Never silently reopen accepted content to satisfy newer editorial criteria.
    candidates={ident:reason for ident,reason in issues.items() if ident in pending_ids}
    on_event('plan_review_completed',issues=len(candidates),reviewed=len(slots))
    # Accepted bytes stay immutable; their violations still block the whole delivery.
    unresolved={ident: reason for ident, reason in issues.items() if ident in accepted_ids}
    if not allow_replan:
        unresolved.update(candidates)
        if candidates:
            on_event('plan_replanning_skipped_first_pass',slots=sorted(candidates))
        return original_slots,unresolved,True
    for ident,reason in candidates.items():
        if repair_targets is not None and ident not in set(repair_targets):
            unresolved[ident]=reason
            continue
        slot=by_id.get(ident)
        if slot is None:
            unresolved[ident]=reason
            continue
        if not ai_content_review_active() and str(slot.get('topic') or '').casefold() in request.casefold():
            unresolved[ident]='Тема явно указана пользователем: '+reason
            continue
        others=[s.get('topic','') for s in slots if slot_id(s)!=ident]
        try:
            alternatives=propose_topic_replacement(ask,model,request,strategy,brief,slot,others,[reason],return_all=True)
        except Exception as exc:
            on_event('plan_replanning_unavailable',publication=ident,error=str(exc)[:200])
            unresolved[ident]=reason
            continue
        for candidate in alternatives if max_candidates is None else alternatives[:max_candidates]:
            if (not isinstance(candidate, dict) or type(candidate.get('week')) is not int
                    or not isinstance(candidate.get('day'),str) or slot_id(candidate)!=ident
                    or candidate.get('format')!=slot.get('format')
                    or topic_repeats(candidate.get('topic'), others)):
                continue
            # The model cannot relax the plan schema while changing a topic.
            # Check the full proposed plan, not just the new title.
            proposed=[candidate if slot_id(s)==ident else s for s in slots]
            if structural_issues(proposed, expected_ids):
                on_event('plan_candidate_rejected',publication=ident,
                         topic=str(candidate.get('topic') or '')[:120],
                         reason='Candidate violates whole-plan structural contract')
                continue
            # independent reviewer of the alternative before accepting a new plan
            single={'artifact_contract':payload['artifact_contract'],
                    'original_request':request[:10000],
                    'request_contract':payload['request_contract'],
                    'business_context':brief,
                    'failed_topic':slot.get('topic'), 'candidate':candidate,
                    'other_topics':others,
                    'instruction':'Проверь только поля плана без учёта старой темы; отмечай явный конфликт с указанием dimension. Не требуй downstream-поля готовой публикации.'}
            try:
                single_text=json.dumps(single,ensure_ascii=False)
                approved=parse_review(invoke_review(single_text,{ident}),{ident})
                if approved is None:
                    on_event('plan_candidate_review_retry',publication=ident,
                             reason='first response did not match slot-scoped review schema')
                    correction=('Предыдущий ответ не прошёл проверку схемы. Проверь только поля плана этого варианта; '
                               'не требуй downstream-поля готовой публикации. Верни JSON {"issues":[]} либо замечание '
                               'с точным ID '+ident+' и разрешённым dimension. Не добавляй другие поля или Markdown.\n\n'+single_text)
                    approved=parse_review(invoke_review(correction,{ident}),{ident})
            except Exception as exc:
                approved=None
                on_event('plan_candidate_review_error',publication=ident,error=str(exc)[:200])
            if approved is None:
                on_event('plan_candidate_review_unavailable',publication=ident,
                         topic=str(candidate.get('topic') or '')[:120],
                         remaining_candidates=max(0,len(alternatives)-alternatives.index(candidate)-1))
                # This is a reviewer/infrastructure failure, not a rejection of
                # this particular topic. Stop the candidate loop and prevent
                # more posts from being written under an unverifiable plan.
                unresolved={slot_name:'Независимая проверка альтернативной темы недоступна'
                            for slot_name in pending_ids}
                return original_slots,unresolved,False
            if approved:
                on_event('plan_candidate_rejected',publication=ident,topic=candidate.get('topic',''),
                         reason=approved[ident]['reason'])
                continue
            for index,old in enumerate(slots):
                if slot_id(old)==ident:
                    slots[index]=candidate
                    break
            by_id[ident]=candidate
            on_event('plan_replanned',publication=ident,old_topic=slot.get('topic'),new_topic=candidate.get('topic'))
            break
        else:
            unresolved[ident]=reason
    # Local candidate approval cannot establish global consistency after mutation.
    if slots != original_slots:
        final_payload={**payload, 'plan':slots, 'instruction':
                       'Повторно проверь ВЕСЬ изменённый план и связи между слотами.'}
        try:
            final=parse_review(invoke_review(json.dumps(final_payload,ensure_ascii=False)),set(by_id))
        except Exception:
            final=None
        if final is None:
            return original_slots,{ident:'Повторный аудит всего изменённого плана недоступен'
                                   for ident in expected_ids},False
        if final:
            final_reasons={ident:item['reason'] for ident,item in final.items()}
            # A known unchanged unresolved slot must not erase unrelated
            # successful edits. A new whole-plan conflict still rolls them back;
            # local approval cannot authorize a globally rejected transaction.
            known_only=all(ident in unresolved and unresolved[ident]==reason
                           for ident,reason in final_reasons.items())
            return (slots if known_only else original_slots),{**unresolved,**final_reasons},True
    return slots,unresolved,True
