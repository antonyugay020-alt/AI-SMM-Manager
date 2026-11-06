# -*- coding: utf-8 -*-
"""Task-level strategy production with bounded, accountable revisions.

A rejected strategy is never treated as approved or reused as if saved. When
resuming a strategy already saved by the user, preserve the original and fail
closed instead of silently replacing it.
"""
from __future__ import annotations
from document_recovery import GeneratedDocument
from strategy_integrity import (strategy_integrity_issues, normalize_explicit_cadence,
                                separate_embedded_publication_section,
                                normalize_unprovided_category,
                                bind_explicit_task_scope)


def _rejection_signature(reason):
    """Compare rejection classes without depending on model-written quotes."""
    import re
    text = str(reason or '')
    # Quotes identify the model's wording, while the text before them names
    # the failed gate. Keeping occurrence counts detects meaningful progress
    # when a repair fixes only some findings.
    parts = re.split(r"(?m)(?=Полное покрытие бизнес-утверждений:|Не подтверждённое|Не подтвержденное|Не распознано|Несоответствие|Отсутствует|Неверн|Недостаточно|Противоречив)", text)
    signatures=[]
    for part in parts:
        part=part.strip()
        if not part:
            continue
        head=re.split(r"(?:цитата|утверждени\w*|фрагмент)\s*:",part,maxsplit=1,flags=re.I)[0]
        head=re.sub(r"[«“\"'].{0,500}?[»”\"']", "<quote>", head)
        head=re.sub(r"\s+", " ", head).strip().casefold()
        if head:
            signatures.append(head[:240])
    return tuple(sorted(signatures)) or (re.sub(r"\s+", " ", text).strip().casefold()[:500],)


def produce_checked_strategy(request, per_week, generate, validate, *,
                             existing=None, report=None, attempts=3, repair=None,
                             review=None, draft=None, checkpoint=None,
                             evidence_context=None, max_revision_calls=None, decode=None):
    from task_semantics import ai_content_review_active
    semantic=ai_content_review_active()
    if type(attempts) is not int or not 1 <= attempts <= 4:
        raise ValueError('Недопустимое число попыток исправления стратегии')
    if not callable(generate) or not callable(validate):
        raise ValueError('Не заданы функции подготовки и проверки стратегии')
    if repair is not None and not callable(repair):
        raise ValueError('Механизм исправления должен быть вызываемым')
    if decode is not None and not callable(decode):
        raise ValueError('Декодер документа должен быть вызываемым')
    if max_revision_calls is not None and (type(max_revision_calls) is not int or max_revision_calls<1):
        raise ValueError('Неверный лимит адресных правок')
    revision_calls=0
    feedback = ''
    rejected_strategy = draft.get('content') if isinstance(draft,dict) else None
    # A persisted rejected artifact is user work awaiting repair. It is not an
    # invitation to discard it and start over. Apply safe host-owned scope
    # normalization before asking a model to edit it, then keep all further
    # repair targeted at this same artifact.
    recovering_saved_draft = bool(rejected_strategy and draft is not None)
    feedback = str(draft.get('reason') or '') if isinstance(draft,dict) else ''
    prior_rejection_signature = _rejection_signature(feedback) if feedback else None
    fresh_after_repair = False
    final = ('', False, 'Стратегия не подготовлена')
    budget = 1 if existing is not None else (1 + attempts if recovering_saved_draft and repair is not None else 1 if recovering_saved_draft else attempts)
    preprocessed_saved_draft = None
    if recovering_saved_draft and existing is None:
        decoded = decode(rejected_strategy) if decode is not None else rejected_strategy
        if isinstance(decoded, GeneratedDocument) and decoded.contract_error:
            preprocessed_saved_draft = decoded
        else:
            prepared_source = decoded.content if isinstance(decoded, GeneratedDocument) else decoded
            prepared, cadence_edits = normalize_explicit_cadence(prepared_source, per_week)
            prepared, scope_edits = separate_embedded_publication_section(prepared)
            if semantic or cadence_edits or scope_edits or prepared_source != rejected_strategy:
                preprocessed_saved_draft = prepared
        if preprocessed_saved_draft is not None and not isinstance(preprocessed_saved_draft, GeneratedDocument):
            if report:
                if cadence_edits:
                    report('strategy_cadence_normalized',attempt=1,edits=cadence_edits[:8])
                if scope_edits:
                    report('strategy_artifact_scope_separated',attempt=1,
                           edits=scope_edits,preserved_original_request=True)
    for attempt in range(1, budget + 1):
        used_targeted_repair = False
        if existing is not None:
            strategy = existing
        elif preprocessed_saved_draft is not None and attempt == 1:
            strategy = preprocessed_saved_draft
            rejected_strategy = strategy
        elif repair is not None and rejected_strategy and feedback and not fresh_after_repair and (draft is not None or attempt > 1):
            # Repair the failed artifact, not a fresh unrelated strategy.
            try:
                if max_revision_calls is not None and revision_calls>=max_revision_calls:
                    break
                revision_calls+=1
                strategy = repair(rejected_strategy, feedback, attempt)
                used_targeted_repair = True
            except Exception as exc:
                if report: report('strategy_patch_failed',attempt=attempt,error=str(exc)[:300])
                # Keep substantive defects AND explain the failed edit to the next attempt.
                root_reason=feedback.split('\nОШИБКА ПРЕДЫДУЩЕЙ ПРАВКИ:',1)[0]
                feedback=root_reason+'\nОШИБКА ПРЕДЫДУЩЕЙ ПРАВКИ: '+str(exc)
                final=(rejected_strategy,False,feedback)
                if checkpoint is not None: checkpoint(*final)
                if not recovering_saved_draft:
                    fresh_after_repair=True
                    if report: report('strategy_fresh_recovery_started',attempt=attempt+1,
                                      cause='targeted_repair_failed',preserved_original_request=True)
                elif attempt < budget and report:
                    report('strategy_targeted_repair_retry',attempt=attempt+1,
                           cause='patch_proposal_failed',same_rejected_document=True)
                continue
        elif rejected_strategy and draft is not None and attempt == 1:
            strategy = rejected_strategy
        else:
            strategy = generate(feedback, attempt)
        if decode is not None and existing is None and not isinstance(strategy, GeneratedDocument):
            strategy = decode(strategy)
        contract_error = ''
        if isinstance(strategy, GeneratedDocument):
            contract_error = strategy.contract_error
            strategy = strategy.content
        if contract_error:
            # A known decoder error has priority over prose heuristics. Retain
            # the exact raw proposal and exact cause for a bounded repair.
            final = (strategy, False, 'Ошибка контракта документа: ' + contract_error)
            if checkpoint is not None and existing is None:
                checkpoint(*final)
            if report:
                report('strategy_response_contract_rejected', attempt=attempt,
                       reason=contract_error, preserved_raw_document=True)
        elif not isinstance(strategy, str) or not strategy.strip():
            final = ('', False, 'Модель не вернула содержимое стратегии')
        else:
            # Publication cadence is explicit structured input. Canonicalize
            # simple affirmative count statements from that value before the
            # validator sees them; never ask a language model to infer it.
            strategy, cadence_edits = (normalize_explicit_cadence(strategy, per_week)
                                       if existing is None else (strategy, []))
            if cadence_edits and report:
                report('strategy_cadence_normalized',attempt=attempt,edits=cadence_edits[:8])
            if existing is None:
                strategy,category_edits=normalize_unprovided_category(strategy,request)
                if category_edits and report:
                    report('strategy_source_field_normalized',attempt=attempt,
                           field='category',edits=category_edits[:5],
                           policy='only_explicit_source_values')
                strategy, scope_edits=separate_embedded_publication_section(strategy)
                if scope_edits and report:
                    report('strategy_artifact_scope_separated',attempt=attempt,
                           edits=scope_edits,preserved_original_request=True)
                strategy, contract_edits=bind_explicit_task_scope(strategy,request,per_week)
                if contract_edits and report:
                    report('strategy_task_scope_bound',attempt=attempt,
                           scope=contract_edits[0])
            valid, reason = ((True,'') if semantic else validate('разработай стратегию. Исходное задание: ' + request, strategy))
            if semantic and review is None:
                valid, reason = False,'Не задан независимый AI-редактор стратегии'
            inconsistencies = strategy_integrity_issues(strategy, request, per_week=per_week,
                                                        evidence_context=evidence_context)
            if inconsistencies:
                valid = False
                reason = '; '.join(inconsistencies)[:1200]
                if report:
                    report('strategy_scope_rejected', attempt=attempt, issues=inconsistencies[:5])
            if valid and review is not None:
                try:
                    verdict=review(strategy)
                    if isinstance(verdict,dict) and verdict.get('review_state')=='unavailable':
                        from review_arbitration import FindingReviewUnavailable
                        raise FindingReviewUnavailable('Независимая проверка стратегии не завершена',
                                                       verdict.get('issues',[]))
                    if not isinstance(verdict,dict) or verdict.get('pass') is not True or verdict.get('issues') != []:
                        valid=False
                        problems=verdict.get('issues',[]) if isinstance(verdict,dict) else []
                        reason='; '.join(str(item.get('reason',''))+'; цитата: '+repr(item.get('quote',''))
                                        for item in problems if isinstance(item,dict)) or 'Независимая проверка стратегии не подтверждена'
                except Exception as exc:
                    valid=False
                    reason='Независимая проверка стратегии недоступна: '+str(exc)
                    if getattr(exc,'review_unavailable',False):
                        final=(strategy,False,str(reason))
                        if checkpoint is not None and existing is None:
                            checkpoint(*final)
                        if report:
                            report('strategy_review_unavailable',attempt=attempt,
                                   reason=str(exc)[:500],preserved_rejected_draft=True)
                        return final
            final = (strategy, bool(valid), '' if valid else str(reason or 'Стратегия не прошла проверку'))
            if checkpoint is not None and existing is None:
                checkpoint(*final)
        if not final[1] and fresh_after_repair and feedback:
            # Keep the root rejection and patch failure visible alongside the
            # fresh candidate's gate result; neither failure supersedes the other.
            final=(final[0],False,(feedback[:900]+'\nСВЕЖАЯ ГЕНЕРАЦИЯ НЕ ПРОШЛА ПРОВЕРКУ: '+final[2])[:1800])
            if checkpoint is not None and existing is None:
                checkpoint(*final)
        if final[1]:
            return final
        if existing is not None:
            if report:
                report('saved_strategy_requires_review', issues=[final[2][:500]])
            break
        if attempt < budget:
            prior_rejected = rejected_strategy
            if isinstance(strategy, str) and strategy.strip():
                rejected_strategy = strategy
            feedback = final[2][:1500]
            current_rejection_signature = _rejection_signature(feedback)
            if (recovering_saved_draft and used_targeted_repair and
                    ((strategy == prior_rejected) if semantic else prior_rejection_signature == current_rejection_signature)):
                # A rejected saved draft remains the recovery source, but the
                # same failed gate after a repair is evidence of no progress.
                # Stop before spending the remaining budget on identical edits.
                if report:
                    report('strategy_recovery_stopped_no_progress', attempt=attempt,
                           issue_signature=list(current_rejection_signature)[:6],
                           preserved_rejected_draft=True)
                return final
            prior_rejection_signature = current_rejection_signature
            if report:
                report('strategy_repair_retry', attempt=attempt + 1,
                       reason=feedback[:500], preserved_original_request=True)
        if used_targeted_repair and not recovering_saved_draft:
            # For an in-memory, unaccepted candidate, a no-op targeted edit
            # switches to one full candidate regeneration from the source
            # brief. A persisted rejected draft follows the durable edit path
            # above and is never replaced by generation. Existing accepted
            # documents returned above without entering repair.
                fresh_after_repair=True
                if report:
                    cause='targeted_repair_no_progress' if strategy == prior_rejected else 'targeted_repair_rejected'
                    report('strategy_fresh_recovery_started',attempt=attempt+1,
                       cause=cause,preserved_original_request=True,
                       preserved_rejected_checkpoint=recovering_saved_draft)
    return final
