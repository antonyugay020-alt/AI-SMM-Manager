"""Shared production entry for GUI and real integration acceptance.

Returned documents still require verified persistence by the caller.
"""
from compound_retry import run_compound_with_retry, invoke_executor
from independent_quality_gate import final_quality_gate, bind_stage_rejection
from independent_acceptance_editor import ValidatedEditorialVerdict
from evidence_context import build_evidence_context
import inspect
import hashlib
import json
from ollama_resilience import model_call_role
from acceptance_policy import final_repair_limit
from model_roles import allocate_revision_roles, model_family, MIN_REVIEWER_FAMILIES
from workflow_recovery import bind_recovery_report, verified_failures, retain_rejected_draft
from reviewer_outcomes import review_unavailable, unavailable_verdict, quarantine_unverified_findings


def _final_rejection_identity(result, editor):
    docs=result.get('documents') if isinstance(result,dict) else []
    material=[]
    for doc in docs if isinstance(docs,list) else []:
        if isinstance(doc,dict):
            material.append((str(doc.get('kind') or ''),str(doc.get('content') or ''),doc.get('valid')))
    material.extend((item['stage'],item['slot'],item['artifact_sha256'])
                    for item in verified_failures(result))
    artifact=hashlib.sha256(json.dumps(material,ensure_ascii=False,
        separators=(',',':')).encode('utf-8')).hexdigest()
    issues=editor.get('issues') if isinstance(editor,dict) else []
    findings=[]
    for issue in issues if isinstance(issues,list) else []:
        if isinstance(issue,dict):
            findings.append((str(issue.get('document') or ''),str(issue.get('type') or ''),
                             ' '.join(str(issue.get('reason') or '').split()).casefold()))
    return artifact,tuple(sorted(findings))


def _actual_review_model(verdict,selected):
    """A dismissal's fresh full review belongs to its actual reviewing model."""
    if verdict.get('full_recheck_after_finding_dismissal'):
        actual=(verdict.get('finding_adjudication') or {}).get('model')
        return actual if isinstance(actual,str) and actual.strip() else None
    return selected


def _invoke_with_evidence(function, *args, evidence_context, **extra_kwargs):
    """Keep injected legacy reviewers usable while production uses one context."""
    try:
        parameters=inspect.signature(function).parameters.values()
        parameters=list(parameters)
        accepts_kwargs=any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters)
        names={p.name for p in parameters}
    except (TypeError,ValueError):
        accepts_kwargs=False
        names=set()
    kwargs={key:value for key,value in extra_kwargs.items()
            if accepts_kwargs or key in names}
    if accepts_kwargs or 'evidence_context' in names:
        kwargs['evidence_context']=evidence_context
    if kwargs:
        return function(*args,**kwargs)
    return function(*args)


def run_document_task(request, context, progress=None, *, execute=None,
                      strategy_reviewer=None, final_reviewer=None, model='qwen3:8b',
                      review_model=None):
    from compound_tasks import execute_compound
    from acceptance_policy import is_first_pass_acceptance
    from independent_acceptance_editor import call_strategy_editor, call_editor, validate_editor_request
    validate_editor_request(request)
    explicit_review_model=review_model is not None
    executor=execute or execute_compound
    preflight=strategy_reviewer or call_strategy_editor
    reviewer=final_reviewer or call_editor
    if review_model is None:
        if execute is None and strategy_reviewer is None and final_reviewer is None:
            from generic_text_review import select_independent_model
            review_model=select_independent_model(model)
        else:
            # Injected executors/reviewers are offline test adapters; preserve
            # their existing contract unless an explicit review model is given.
            review_model=model
    if (execute is None and strategy_reviewer is None and final_reviewer is None
            and model_family(review_model)==model_family(model)):
        raise ValueError('Автор и независимый редактор должны принадлежать разным семействам моделей')
    if (explicit_review_model and execute is None and strategy_reviewer is None and final_reviewer is None):
        from review_qualification import probe_qualification, ReviewerQualificationUnavailable
        qualification=probe_qualification(review_model)
        if not qualification['pass']:
            raise ReviewerQualificationUnavailable('Назначенный редактор не прошёл контрольные решения',[qualification])
    from task_provenance import TaskProvenance
    provenance=TaskProvenance.from_request(request)
    previous=context.get("task_provenance") if isinstance(context,dict) else None
    if previous is not None:
        TaskProvenance.restore(previous).verify(request)
    state=dict(context)
    state["task_provenance"]=provenance.as_dict()
    contributing_writers={model}
    state['plan_reviewer_model']=review_model
    state['independent_editor_model']=review_model
    review_models=[review_model]
    qualification_results=[]
    if execute is None and strategy_reviewer is None and final_reviewer is None:
        try:
            from generic_text_review import select_independent_model
            backup=select_independent_model(model,exclude_models=review_models)
            if backup.casefold()!=str(review_model).casefold():
                review_models.append(backup)
                if progress:
                    progress({'stage':'independent_reviewer_backup_selected',
                              'primary_model':review_model,'backup_model':backup})
        except (ValueError,OSError) as exc:
            qualification_results.extend(getattr(exc,'results',[]))
            if progress:
                progress({'stage':'independent_reviewer_backup_unavailable',
                          'reason':str(exc)[:220]})
    if (execute is None and strategy_reviewer is None and final_reviewer is None
            and len({model_family(name) for name in review_models})<MIN_REVIEWER_FAMILIES):
        from review_qualification import ReviewerQualificationUnavailable
        from generic_text_review import qualification_records
        qualification_results=qualification_records(review_models)+qualification_results
        raise ReviewerQualificationUnavailable('Для автора нужны два независимых квалифицированных редактора; генерация не начата',
            qualification_results)
    state['plan_reviewer_models']=review_models
    if execute is None and strategy_reviewer is None and final_reviewer is None:
        from generic_text_review import known_quality_model_pool
        state['quality_model_pool']=known_quality_model_pool(model,dict(enumerate(review_models)))
    if progress:
        progress({'stage':'independent_reviewer_selected','author_model':model,
                  'reviewer_model':review_model})
    # Build once at the orchestration boundary. Every component receives the
    # same provenance-bearing, immutable evidence contract.
    evidence_context=build_evidence_context(str(request),state).to_dict()
    state['evidence_context']=evidence_context
    state['strict_document_recovery']=True
    state['strategy_review_findings']=[]
    def review_report(stage, **details):
        if progress:
            progress({'stage':stage,**details})
    def validate_native_findings(verdict,candidate,req,fields,raw_review):
        from review_arbitration import validate_editorial_verdict, FindingReviewUnavailable
        from ollama_resilience import request_chat
        backup=next((name for name in review_models
                     if model_family(name)!=model_family(candidate)
                     and all(model_family(name)!=model_family(writer) for writer in contributing_writers)),None)
        def ask_judge(reviewer_model,system,user,*,json_schema):
            if not backup:
                raise FindingReviewUnavailable('Нет независимой модели для проверки применимости замечаний')
            response=request_chat({'model':reviewer_model,'stream':False,'think':False,
                'format':json_schema,'messages':[{'role':'system','content':system},
                {'role':'user','content':user}],
                'options':{'num_ctx':16384,'temperature':0}},
                stage='проверка применимости замечаний документов',attempts=1,report=review_report)
            return response.get('message',{}).get('content','')
        return validate_editorial_verdict(ask_judge,backup,str(req),fields,verdict,
            full_review=lambda:raw_review(backup),source_context=evidence_context,report=review_report)

    def strategy_review(body):
        extra={'require_inventory':True} if preflight is call_strategy_editor else {}
        if preflight is call_strategy_editor:
            extra['report']=review_report
        verdict=None
        for index,candidate in enumerate(review_models):
            call_extra=dict(extra)
            if preflight is call_strategy_editor:
                call_extra['model']=candidate
            try:
                with model_call_role('reviewer_backup' if index>0 else 'reviewer'):
                    verdict=_invoke_with_evidence(preflight,str(request),body,
                                                  evidence_context=evidence_context,**call_extra)
                if preflight is call_strategy_editor:
                    def raw_strategy_review(backup):
                        return _invoke_with_evidence(preflight,str(request),body,
                            evidence_context=evidence_context,require_inventory=True,
                            model=backup,report=review_report)
                    verdict=validate_native_findings(verdict,candidate,request,
                        {'strategy':body},raw_strategy_review)
                if (review_unavailable(verdict)
                        and isinstance(verdict,dict) and verdict.get('applicability_review_complete')):
                    state['strategy_review_findings'][:]=verdict.get('issues',[])
                    from review_arbitration import FindingReviewUnavailable
                    raise FindingReviewUnavailable('Проверка применимости стратегии не завершена',
                                                   verdict.get('issues',[]))
                if (review_unavailable(verdict) and index+1<len(review_models)):
                    review_report('independent_reviewer_failover',stage_name='strategy',
                                  from_model=candidate,to_model=review_models[index+1])
                    continue
                break
            except Exception as exc:
                if getattr(exc,'review_unavailable',False) and isinstance(getattr(exc,'findings',None),list):
                    state['strategy_review_findings'][:]=exc.findings
                if (not getattr(exc,'review_unavailable',False) or not getattr(exc,'retryable',True)
                        or index+1>=len(review_models)):
                    raise
                review_report('independent_reviewer_failover',stage_name='strategy',
                              from_model=candidate,to_model=review_models[index+1],
                              error=str(exc)[:200])
        
        if isinstance(verdict,dict) and verdict.get('pass') is not True:
            issues=verdict.get('issues',[])
            if isinstance(issues,list):
                state['strategy_review_findings'][:]=[x for x in issues if isinstance(x,dict)]
        elif isinstance(verdict,dict) and verdict.get('pass') is True:
            state['strategy_review_findings'].clear()
        return verdict
    state['strategy_reviewer']=strategy_review
    # Publications are quality-checked and checkpointed as recoverable drafts
    # by the executor. The source-backed business-claim inventory belongs to
    # the complete-delivery review below, where it can see the full strategy,
    # calendar and their shared context. Running the whole inventory per post
    # multiplied model calls by calendar size and duplicated the final gate.
    # Fast acceptance deliberately measures first-pass quality. Full-profile
    # acceptance must also exercise the same bounded recovery as production;
    # its first-pass score remains separate from final delivery. Do not reset
    # task-scoped checkpoints or accepted documents between attempts.
    retry_budget=1 if is_first_pass_acceptance(state) else 2
    result=run_compound_with_retry(executor,request,state,progress=progress,
                                   max_attempts=retry_budget,model=model)
    result=bind_stage_rejection(result,state['strategy_review_findings'])
    result=bind_recovery_report(result)
    if progress: progress({'stage':'final_independent_editor'})
    def review(req, strategy, calendar):
        primary_result=None
        unavailable_reviewers=set()
        for index,candidate in enumerate(review_models):
            kwargs={'evidence_context':evidence_context}
            if reviewer is call_editor:
                kwargs.update({'model':candidate,'report':review_report,'require_inventory':True})
            try:
                with model_call_role('reviewer_backup' if index>0 else 'reviewer'):
                    result=_invoke_with_evidence(reviewer,req,strategy,calendar,**kwargs)
            except Exception as exc:
                # Failure of an independent judge is not a defect in the draft.
                # Try another qualified family, if one was admitted at launch.
                if (not getattr(exc,'review_unavailable',False)
                        and not isinstance(exc,(OSError,TimeoutError,ConnectionError))
                        and not (reviewer is call_editor and isinstance(exc,(ValueError,TypeError)))):
                    raise
                result=unavailable_verdict(type(exc).__name__+': '+str(exc),
                    getattr(exc,'findings',None))
                unavailable_reviewers.add(model_family(candidate))
                review_report('independent_reviewer_unavailable',stage_name='final',
                              model=candidate,reason=type(exc).__name__)
            if review_unavailable(result):
                unavailable_reviewers.add(model_family(candidate))
                result=quarantine_unverified_findings(result)
            if reviewer is call_editor and not review_unavailable(result):
                def raw_final_review(backup):
                    return _invoke_with_evidence(reviewer,req,strategy,calendar,
                        evidence_context=evidence_context,model=backup,
                        report=review_report,require_inventory=True)
                try:
                    result=validate_native_findings(result,candidate,req,
                        {'strategy':strategy,'calendar':calendar},raw_final_review)
                except ValueError as exc:
                    # A malformed primary applicability verdict is an unavailable
                    # review, not an exception that can abort the whole workflow.
                    result=unavailable_verdict('Ошибка контракта основного редактора: '+str(exc)[:300])
                    unavailable_reviewers.add(model_family(candidate))
                    review_report('primary_editor_contract_unavailable',
                        model=candidate,reason=str(exc)[:200])
                # A failed primary review must not prevent an already qualified
                # independent backup from checking the SAME immutable draft.
                if result.get('pass') and len(review_models)>1:
                    from independent_acceptance_editor import call_substance_reader
                    opinion_model=_actual_review_model(result,candidate)
                    if not opinion_model:
                        from review_arbitration import FindingReviewUnavailable
                        return unavailable_verdict('Не установлена модель итогового основного мнения')
                    reader_model=next((name for name in review_models
                        if model_family(name)!=model_family(opinion_model)
                        and model_family(name) not in unavailable_reviewers
                        and all(model_family(name)!=model_family(writer) for writer in contributing_writers)),None)
                    if reader_model is None:
                        # A judge whose review failed cannot be silently reused
                        # as the second independent opinion on identical bytes.
                        # Acquire a NEW qualified family or fail closed.
                        from generic_text_review import select_independent_model
                        excluded=[*contributing_writers,*review_models]
                        try:
                            spare=select_independent_model(model,exclude_models=excluded)
                            if (not isinstance(spare,str) or not spare.strip()
                                    or model_family(spare) in {model_family(x) for x in excluded}):
                                raise ValueError('Не найдена новая независимая семья')
                            reader_model=spare
                            review_report('substance_reader_replaced_unavailable',
                                replacement=reader_model,failed_families=sorted(unavailable_reviewers))
                        except (ValueError,OSError,StopIteration) as exc:
                            from review_arbitration import FindingReviewUnavailable
                            return unavailable_verdict('Нет независимой доступной модели для содержательного чтения: '+str(exc)[:200])
                    reading_attempts=[]
                    def read_body(reader):
                        with model_call_role('reviewer_backup'):
                            return call_substance_reader(req,strategy,calendar,model=reader,
                                evidence_context=evidence_context,report=review_report)
                    # At most one additional qualified family may supply an
                    # absent independent opinion. Never retry opinions until
                    # agreement, or count the primary model's reread twice.
                    for reading_attempt in range(2):
                        review_report('substance_reader_started',model=reader_model,primary_model=opinion_model)
                        try:
                            reading=read_body(reader_model)
                            reading=validate_native_findings(reading,reader_model,req,
                                {'strategy':strategy,'calendar':calendar},read_body)
                        except Exception as exc:
                            # The second opinion is not evidence of acceptance if
                            # its schema or applicability contract is malformed.
                            # Keep the draft blocked and preserve the reason.
                            reading=ValidatedEditorialVerdict({'pass':False,
                                'review_state':'unavailable','issues':[{
                                    'document':'review','type':'review_unavailable',
                                    'adjudication':'unavailable','quote':'',
                                    'reason':'Ошибка контракта независимого чтения: '+str(exc)[:300]}]})
                            review_report('substance_reader_contract_unavailable',
                                model=reader_model,reason=str(exc)[:200])
                        actual_reader=_actual_review_model(reading,reader_model)
                        independent=bool(actual_reader and model_family(actual_reader)!=model_family(opinion_model)
                            and all(model_family(actual_reader)!=model_family(writer) for writer in contributing_writers))
                        reading_attempts.append({'requested_model':reader_model,'actual_model':actual_reader,
                            'independent':independent,'pass':reading.get('pass') is True})
                        if reading.get('pass') and not independent:
                            if reading_attempt==0:
                                from generic_text_review import select_independent_model
                                excluded=[*contributing_writers,*review_models,reader_model,opinion_model]
                                try:
                                    spare=select_independent_model(model,exclude_models=excluded)
                                    if (not isinstance(spare,str) or not spare.strip() or
                                            model_family(spare) in {model_family(name) for name in excluded}):
                                        raise ValueError('Дополнительная модель не независима от существующих ролей')
                                except (ValueError,OSError) as exc:
                                    review_report('substance_reader_capacity_unavailable',reason=str(exc)[:220])
                                else:
                                    reader_model=spare
                                    continue
                            reading=ValidatedEditorialVerdict({**reading,'pass':False,'review_state':'unavailable',
                                'issues':[{'document':'review','type':'review_unavailable','quote':'',
                                    'adjudication':'unavailable','reason':'Не получено второе независимое содержательное мнение: '
                                    'после разрешения замечаний итоговое чтение выполнено моделью основного аудита.'}]})
                        break
                    metadata={'model':actual_reader,'requested_model':reader_model,'independent_from':opinion_model,
                        'independence_verified':independent,'attempts':reading_attempts,
                        'status':'PASS' if reading.get('pass') and independent else 'BLOCKED',
                        'substance_review':reading.get('substance_review')}
                    review_report('substance_reader_completed',model=actual_reader,
                        status=metadata['status'],issue_count=len(reading.get('issues',[])))
                    result=ValidatedEditorialVerdict({**result,'independent_reader':metadata})
                    if not reading.get('pass'):
                        result['pass']=False;result['issues']=reading.get('issues',[])
                        result['review_state']=reading.get('review_state','complete')
                        result.update({key:reading[key] for key in ('finding_adjudication',
                            'applicability_review_complete','review_transport_unavailable',
                            'review_control_unavailable') if key in reading})
                        return result
                if (isinstance(result,dict) and result.get('applicability_review_complete')
                        and index==0 and not review_unavailable(result)):
                    return result
            unavailable=review_unavailable(result)
            if index==0:
                primary_result=result
                if unavailable and index+1<len(review_models):
                    review_report('independent_reviewer_failover',stage_name='final',
                                  from_model=candidate,to_model=review_models[index+1])
                    continue
                return result
            if not isinstance(primary_result,dict):
                return result
            # Preserve findings from every completed subreview, including when
            # both reviewers are partially unavailable. Never promote raw data
            # to the host-validated trust marker merely by merging results.
            combined=[]
            seen=set()
            sources=(primary_result,result)
            for source in sources:
                items=source.get('issues',[])
                for item in items if isinstance(items,list) else []:
                    diagnostic=(isinstance(item,dict) and (item.get('type')=='review_unavailable'
                        or item.get('adjudication') in ('unavailable','invalid_decision')))
                    if not isinstance(item,dict) or (diagnostic and not unavailable):
                        continue
                    key=json.dumps(item,ensure_ascii=False,sort_keys=True)
                    if key not in seen:
                        combined.append(item);seen.add(key)
            trusted=True
            for source in sources:
                items=source.get('issues',[])
                substantive=any(isinstance(item,dict) and item.get('type')!='review_unavailable'
                    for item in items if isinstance(items,list))
                if substantive and not isinstance(source,ValidatedEditorialVerdict):
                    trusted=False
            cls=ValidatedEditorialVerdict if trusted and isinstance(result,ValidatedEditorialVerdict) else dict
            merged=cls(result)
            merged['issues']=combined;merged['pass']=(result.get('pass') is True and not combined and not unavailable)
            merged['review_state']='unavailable' if unavailable else 'complete'
            return merged
        return primary_result
    result=final_quality_gate(result,request,review)
    # Two bounded correction cycles for final-review findings. These artifacts
    # are still task drafts: retain a rejected checkpoint, repair only cited
    # slots, and carry forward user-approved documents unchanged.
    report=result.get('content_delivery') if isinstance(result,dict) else None
    editor=report.get('independent_editor') if isinstance(report,dict) else None
    def report_final_review(editor_state, attempt):
        if progress:
            issues=editor_state.get('issues',[]) if isinstance(editor_state,dict) else []
            progress({'stage':'final_review_result','attempt':attempt,
                      'status':editor_state.get('status') if isinstance(editor_state,dict) else 'missing',
                      'review_state':editor_state.get('review_state') if isinstance(editor_state,dict) else 'missing',
                      'issue_count':len(issues) if isinstance(issues,list) else 0,
                      'repair_slots':list(editor_state.get('repair_slots') or []) if isinstance(editor_state,dict) else [],
                      'affected_documents':sorted({str(item.get('document')) for item in issues
                          if isinstance(item,dict) and item.get('document')}) if isinstance(issues,list) else []})
    report_final_review(editor,1)
    reviewed_rejections=set()
    repair_round_limit=final_repair_limit(state,2)
    if (repair_round_limit==0 and isinstance(editor,dict) and
            editor.get('status')=='BLOCKED' and progress):
        progress({'stage':'autonomous_retry_skipped_acceptance_sample',
                  'reason':'This explicit first-pass benchmark does not permit final draft repair.'})
    for repair_round in range(1,repair_round_limit+1):
        if not (isinstance(editor,dict) and editor.get('status')=='BLOCKED'):
            break
        if editor.get('review_state')=='unavailable' and not editor.get('issues'):
            break
        rejection_identity=_final_rejection_identity(result,editor)
        if rejection_identity in reviewed_rejections:
            if progress:
                progress({'stage':'autonomous_retry_stopped_no_progress',
                          'attempt':repair_round+1,
                          'reason':'Повторная независимая проверка вернула тот же документ и те же замечания; сохранённый результат оставлен без изменений'})
            break
        reviewed_rejections.add(rejection_identity)
        issues=editor.get('issues',[])
        existing=list(state.get('resume_documents') or [])
        saved_kinds={d.get('kind') for d in existing
                     if isinstance(d,dict) and d.get('already_saved') is True}
        affected={i.get('document') for i in issues if isinstance(i,dict)}
        repair_slots=list(editor.get('repair_slots') or [])
        has_unapproved_strategy=('strategy' in affected and 'strategy' not in saved_kinds)
        has_addressable_calendar=('calendar' in affected and bool(repair_slots)
                                  and 'calendar' not in saved_kinds)
        # A saved/accepted result is immutable. It remains blocked for explicit
        # user revision; only unaccepted output may enter autonomous repair.
        if not (has_unapproved_strategy or has_addressable_calendar):
            if progress:
                reason=('no_addressable_calendar_slot' if 'calendar' in affected and not repair_slots
                        else 'only_saved_documents_are_affected' if affected & saved_kinds
                        else 'no_unapproved_repair_target')
                progress({'stage':'autonomous_retry_not_started','attempt':repair_round+1,
                          'reason':reason,'affected_documents':sorted(str(x) for x in affected),
                          'repair_slots':repair_slots,'saved_kinds':sorted(str(x) for x in saved_kinds)})
            break
        if has_unapproved_strategy or has_addressable_calendar:
            if isinstance(result,dict):
                for document in result.get('documents',[]):
                    if (isinstance(document,dict) and document.get('kind')=='strategy'
                            and document.get('valid') is True
                            and document.get('kind') not in affected
                            and not any(isinstance(item,dict) and item.get('kind')=='strategy'
                                        for item in existing)):
                        existing.append({'kind':'strategy','content':document['content'],
                                         'already_saved':document.get('already_saved') is True})
            repair_state=dict(state)
            repair_state['task_mode']='final_review_repair'
            repair_state['resume_documents']=existing
            repair_state['final_review_repair_slots']=repair_slots
            repair_state['final_review_issues_by_slot']=dict(editor.get('repair_issues_by_slot') or {})
            repair_state['final_review_issues']=issues
            stage_failures=verified_failures(result)
            candidate_context=result.get('recovery_context')
            if (isinstance(candidate_context,dict) and
                    candidate_context.get('task_signature')==result.get('task_signature')):
                import copy
                recovery=copy.deepcopy(candidate_context)
                recovery['plan_targets']=[f['slot'] for f in stage_failures if f['stage']=='plan']
                repair_state['workflow_recovery']=recovery
                repair_state['preserved_stage_documents']=[dict(d) for d in result.get('documents',[])
                    if isinstance(d,dict) and d.get('valid') is True and d.get('kind') not in affected]
            repair_state['rejected_documents']=[dict(d) for d in result.get('documents', [])
                if isinstance(d,dict) and d.get('kind') in affected
                and d.get('already_saved') is not True]
            repair_state['previous_rejection_issues']='; '.join(
                str(i.get('reason') or '')[:240] for i in issues if isinstance(i,dict))[:1600]
            if progress:
                progress({'stage':'autonomous_retry','attempt':repair_round+1,
                          'reason':'Итоговая редактура нашла конкретные проблемы; исправляются только незавершённые черновики'})
            role_pool=list(state.get('quality_model_pool') or [])
            if execute is None and strategy_reviewer is None and final_reviewer is None:
                try:
                    from generic_text_review import select_independent_model
                    spare=select_independent_model(model,exclude_models=review_models)
                    role_pool=list(dict.fromkeys([*role_pool,spare]))
                except (ValueError,OSError):
                    pass
            try:
                roles=allocate_revision_roles(model,role_pool,
                    fixed_reviewers={0:review_models[0]},required_reviewers=len(review_models),
                    previous_writers=contributing_writers)
            except (ValueError, LookupError) as exc:
                # An unavailable qualified revision role must not discard the
                # already-reviewed rejected draft. Keep it blocked, retain its
                # independent findings, and let the caller persist the result.
                if progress:
                    progress({'stage':'autonomous_retry_capacity_blocked',
                              'attempt':repair_round+1,'reason':str(exc)[:300],
                              'preserved_rejected_draft':True})
                break
            contributing_writers.add(roles.revision_author)
            if model_family(roles.revision_author)!=model_family(model):
                # The primary reviewer stays independent. An internally assigned
                # backup may become the reviser; replace its review role before
                # any strategy, publication or final review of these new bytes.
                prior_review_count=len(review_models)
                excluded_families={model_family(m) for m in contributing_writers}
                review_models[:]=[m for m in review_models if model_family(m) not in excluded_families]
                while len(review_models)<prior_review_count:
                    from generic_text_review import select_independent_model
                    try:
                        backup=select_independent_model(roles.revision_author,candidates=roles.reviewer_pool,
                            exclude_models=[*contributing_writers,*review_models])
                    except ValueError:
                        break
                    review_models.append(backup)
                required_count=(MIN_REVIEWER_FAMILIES if execute is None and strategy_reviewer is None and final_reviewer is None else prior_review_count)
                if len({model_family(name) for name in review_models})<required_count:
                    raise ValueError('После назначения автора правки недостаточно независимых редакторов')
                repair_state.update(plan_reviewer_model=review_models[0],plan_reviewer_models=list(review_models),
                    publication_reviewer_models=list(review_models),independent_editor_model=review_models[0])
            if progress:
                progress({'stage':'text_revision_roles_selected','initial_author_model':model,
                          'revision_author_model':roles.revision_author,'reason':roles.reason})
            repaired=invoke_executor(executor,request,repair_state,progress=progress,
                                     model=roles.revision_author)
            repaired=bind_stage_rejection(repaired,state['strategy_review_findings'])
            repaired=bind_recovery_report(repaired)
            retained,kept=retain_rejected_draft(result,repaired,repair_round)
            if kept:
                result=retained
                if progress:
                    progress({'stage':'failed_repair_draft_retained','attempt':repair_round,
                              'reason':'replacement_lost_checked_publications',
                              'previous_rejection_preserved':True})
                break
            if progress: progress({'stage':'final_independent_editor','attempt':repair_round+1})
            result=final_quality_gate(repaired,request,review)
            if isinstance(result,dict):
                result['generation_roles']={'initial_author':model,
                    'revision_author':roles.revision_author,'reviewers':list(review_models)}
            report=result.get('content_delivery') if isinstance(result,dict) else None
            editor=report.get('independent_editor') if isinstance(report,dict) else None
            report_final_review(editor,repair_round+1)
    # Bind the reviewed artifact and its durable audit to the same immutable
    # request identity before either is exposed or committed. This prevents
    # recovery from silently treating a report for another request as current.
    if isinstance(result, dict):
        result['task_provenance'] = provenance.as_dict()
        delivery = result.get('content_delivery')
        if isinstance(delivery, dict):
            existing_identity = delivery.get('task_provenance')
            if existing_identity is not None:
                TaskProvenance.restore(existing_identity).verify(request)
            delivery['task_provenance'] = provenance.as_dict()
    # One full-identity, project-scoped authority is shared with the draft writer.
    signature=result.get('task_signature') if isinstance(result,dict) else None
    if signature and state.get('project_workspace') and isinstance(result.get('content_delivery'),dict):
        from delivery_report_store import store_delivery_report
        result['content_delivery']['task_signature'] = signature
        store_delivery_report(state['project_workspace'],signature,result['content_delivery'],
                              request=request)
    if isinstance(result,dict):
        result.setdefault('generation_roles',{'initial_author':model,'revision_author':None,
                                             'reviewers':list(review_models)})
    return result
