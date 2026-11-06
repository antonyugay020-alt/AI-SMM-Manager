# -*- coding: utf-8 -*-
"""Execute small synthetic workflows through the actual production pipeline.
No personal project data and no database mutations outside temp sandbox.
"""
from acceptance_policy import workflow_outcome_metrics
from model_roles import MIN_REVIEWER_FAMILIES, authors_with_review_capacity
import json
import pathlib
import sys
import tempfile
import traceback
import os
import secrets
import re
import random
import time
from datetime import datetime,timezone
from candidate_identity import CANDIDATE_ID
from random_acceptance_tasks import build_randomized_workflow_cases, select_acceptance_author_models, select_profile_author_models

AUTHOR_CASE_BUDGET_SECONDS=390
AUTHOR_CALL_BUDGET_SECONDS=90
REVIEWER_RESERVE_SECONDS=60
REVIEWER_BACKUP_RESERVE_SECONDS=45

def eligible_author_models(installed_models, capability_models=None):
    """Keep structured-output-incompatible Ollama tags out of costly author cases.

    The Windows runner probes each installed family before live acceptance. API
    models remain eligible only when their preflight gate enabled acceptance.
    With no probe result (offline unit use), retain the supplied model list.
    """
    installed=list(dict.fromkeys(str(x).strip() for x in installed_models or [] if str(x).strip()))
    if capability_models is None:
        return installed, []
    if not isinstance(capability_models,(list,tuple,set)) or not all(
            isinstance(x,str) and x.strip() for x in capability_models):
        raise ValueError('Список прошедших capability probe моделей некорректен')
    passed={x.strip() for x in capability_models}
    eligible=[];excluded=[]
    for model in installed:
        # Every provider route, including cloud API routes, must be explicitly
        # present in the capability-approved pool. The Windows runner adds an
        # API model only after its own provider preflight succeeds.
        if model in passed:
            eligible.append(model)
        else:
            excluded.append(model)
    return eligible,excluded

def validate_standalone_review_capacity(cases, reviewer_models):
    """Fail before live generation if a workflow author lacks two independent reviewer families."""
    from generic_text_review import _model_family
    pool=list(dict.fromkeys(str(x).strip() for x in reviewer_models or [] if str(x).strip()))
    families={_model_family(name) for name in pool}
    for case in cases or []:
        if not isinstance(case,dict) or case.get('workflow_type') not in ('standalone_post','compound_strategy_calendar'):
            continue
        author=str(case.get('author_model') or '').strip()
        available=families-{_model_family(author)} if author else families
        if len(available)<MIN_REVIEWER_FAMILIES:
            raise ValueError(
                f"Недостаточно независимых рецензентов для автора {author or '(не указан)'}: "
                f"нужно {MIN_REVIEWER_FAMILIES} семейства помимо автора, доступно {len(available)}")
    return True

def normalize_progress_event(item=None, **details):
    """Accept both dict-style pipeline events and stage/keyword transport events."""
    if isinstance(item,dict):
        event=dict(item)
        event.update(details)
        return event
    if item is None:
        return dict(details)
    return {'stage':str(item),**details}

def run_recovery_cases(target):
    """Real Ollama recovery of exact rejected A2 strategies; never regenerate."""
    import hashlib
    from document_recovery import DocumentDraft, patch_document
    from strategy_recovery import produce_checked_strategy
    from strategy_integrity import strategy_integrity_issues
    from independent_acceptance_editor import call_strategy_editor
    from compound_tasks import _ask
    from agent_runtime import validate_document
    from content_requirements import publication_scope
    test_source=pathlib.Path(os.environ.get('AI_SMM_TEST_SOURCE') or
                             (pathlib.Path(__file__).parent.parent/'AI_SMM_Developer_A28'/'tests')).resolve()
    fixture=test_source/'a2_recovery_fixture.json'
    if not fixture.is_file():
        raise FileNotFoundError('В developer test source отсутствует a2_recovery_fixture.json: '+str(test_source))
    cases=json.loads(fixture.read_text(encoding='utf-8-sig'))
    results=[]
    for case in cases:
        item={'name':case['name'],'status':'FAIL'}
        events=[]
        try:
            folder=target/('recovery_'+case['name'])
            folder.mkdir(exist_ok=True)
            signature=hashlib.sha256(case['request'].encode('utf-8')).hexdigest()
            frequency=publication_scope(case['request']).per_week
            problems=strategy_integrity_issues(case['strategy'],case['request'],per_week=frequency)
            if not problems: raise ValueError('Recovery seed must contain a detected defect')
            DocumentDraft(folder,signature,'strategy').save(case['strategy'],False,'; '.join(problems))
            # Reopen durable state; do not carry the original object into recovery.
            reopened=DocumentDraft(folder,signature,'strategy')
            def event(stage,**kwargs):events.append({'stage':stage,**kwargs})
            def generate(*args):raise AssertionError('Rejected draft must not be regenerated')
            def repair(body,reason,attempt):
                return patch_document(lambda m,s,u,**kw:_ask(m,s,u,report=event,**kw),
                    'qwen3:8b',case['request'],body,reason,constrained=True)
            content,valid,reason=produce_checked_strategy(case['request'],frequency,generate,
                validate_document,draft=reopened.load(),repair=repair,checkpoint=reopened.save,
                review=lambda body:call_strategy_editor(case['request'],body,require_inventory=True),report=event)
            receipt=DocumentDraft(folder,signature,'strategy').load()
            item.update(status='PASS' if valid and receipt['content']==content and receipt['valid'] else 'FAIL',
                        reason=reason,content=content,checkpoint_verified=receipt['content']==content,
                        fresh_generation_forbidden=True)
        except Exception as exc:
            item['error']=type(exc).__name__+': '+str(exc)
        item['events']=events
        results.append(item)
    (target/'strategy_recovery_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    return results


def run():
    from compound_tasks import execute_compound
    from text_workflow import run_text_task
    from generic_text_review import (acceptance_model_pool, list_installed_ollama_models,
        select_independent_reviewers, _independent_panel, _model_family)
    from text_providers import configured_cloud_models, is_ollama_cloud_tag
    import os
    target=pathlib.Path(os.environ['AI_SMM_TEST_ROOT'])
    target.mkdir(parents=True,exist_ok=True)
    # A fresh seed makes every acceptance run exercise new task combinations.
    # Set AI_SMM_ACCEPTANCE_SEED to replay a specific reported run exactly.
    seed_text=os.environ.get('AI_SMM_ACCEPTANCE_SEED','').strip()
    task_seed=int(seed_text) if seed_text else secrets.randbits(64)
    installed_models=list_installed_ollama_models()
    cloud_enabled=(os.environ.get('AI_SMM_ENABLE_FREE_CLOUD_ACCEPTANCE','').strip()=='1' or
                   os.environ.get('AI_SMM_ENABLE_OPENAI_ACCEPTANCE','').strip()=='1')
    if cloud_enabled:
        installed_models.extend(configured_cloud_models())
    installed_models=list(dict.fromkeys(installed_models))
    capability_pool_text=os.environ.get('AI_SMM_ACCEPTANCE_AUTHOR_MODELS','').strip()
    capability_pool=(json.loads(capability_pool_text) if capability_pool_text else None)
    installed_author_models=list(installed_models)
    installed_models,excluded_author_models=eligible_author_models(
        installed_models,capability_pool)
    if not installed_models:
        raise ValueError('Нет моделей-авторов, прошедших проверку структурированного ответа')
    reviewer_pool_text=os.environ.get('AI_SMM_ACCEPTANCE_REVIEWER_MODELS','').strip()
    reviewer_pool=(json.loads(reviewer_pool_text) if reviewer_pool_text else installed_models)
    if (not isinstance(reviewer_pool,list) or
        not all(isinstance(name,str) and name in installed_author_models for name in reviewer_pool)):
        raise ValueError('Набор проверенных моделей-рецензентов некорректен')
    # Direct runner launches use the same admission as the GUI and supervisor.
    # An inherited legacy JSON-only allowlist cannot certify reviewer reasoning.
    from review_qualification import probe_qualification
    reviewer_qualifications=[probe_qualification(name) for name in reviewer_pool]
    reviewer_pool=[row['model'] for row in reviewer_qualifications if row['pass']]
    if not reviewer_pool:
        raise ValueError('Нет редакторов, прошедших контрольные решения; генерация не начата')
    installed_models,deferred_author_models=authors_with_review_capacity(installed_models,reviewer_pool)
    if not installed_models:
        raise ValueError('Нет автора с двумя независимыми квалифицированными редакторами')
    # Every installed tag participates; stable ordering makes seed replay map
    # each generated random task to the same author model.
    profile=os.environ.get('AI_SMM_TEST_PROFILE','full').strip().casefold()
    author_order=random.Random(task_seed ^ 0xA75A017)
    author_model_pool=select_acceptance_author_models(installed_models,chooser=random.Random(task_seed ^ 0xA75A016))
    author_models=select_profile_author_models(installed_models,profile,chooser=author_order)
    reviewer_randomizer=random.Random(task_seed ^ 0xA75C0FFEE)
    reviewer_models=select_independent_reviewers(author_models,candidates=reviewer_pool,
                                                   chooser=reviewer_randomizer)
    ollama_cloud_models=[m for m in author_models if is_ollama_cloud_tag(m)]
    api_cloud_models=[m for m in author_models if '/' in m]
    # Every author model gets a different random brief in this run. The seed
    # replays task/model assignments, while a new seed changes both.
    briefs=build_randomized_workflow_cases(task_seed,author_models,ensure_workflow_coverage=True)
    for index,case in enumerate(briefs,1):
        model=case['author_model']
        family=re.sub(r'[^a-z0-9_-]+','_',model.split(':',1)[0].casefold()).strip('_') or 'model'
        case['name']=f"qa_{index:02d}_{case['workflow_type']}_{family}"
        case['author_model']=model
        case['review_model']=(reviewer_models[model]
                              if case['workflow_type']=='compound_strategy_calendar' else None)
    validate_standalone_review_capacity(briefs,reviewer_pool)
    results={'build':CANDIDATE_ID,
             'timestamp':datetime.now(timezone.utc).isoformat(),
             'task_generation':{'mode':'independent_seeded_random_synthetic_briefs','seed':task_seed,
                                'paired_comparison':False,
                                'different_brief_per_author_model':True,
                                'new_random_briefs_each_acceptance_run':True,
                                'profile':profile,
                                'case_count':len(briefs),'selected_author_model_count':len(author_models),
                                'author_model_pool':author_model_pool,
                                'installed_author_model_pool':installed_author_models,
                                'author_models_excluded_by_capability_probe':excluded_author_models,
                                'author_model_pool_count':len(author_model_pool),
                                'coverage_complete':set(author_models)==set(author_model_pool) and not deferred_author_models,
                                'eligible_author_coverage_complete':set(author_models)==set(author_model_pool),
                                'author_models_deferred_for_review_capacity':deferred_author_models,
                                'author_models_tested':author_models,
                                'cloud_acceptance_enabled':bool(cloud_enabled or ollama_cloud_models),
                                'api_cloud_acceptance_enabled':cloud_enabled,
                                'ollama_cloud_acceptance_enabled':bool(ollama_cloud_models),
                                'cloud_models_tested':sorted(set(ollama_cloud_models+api_cloud_models)),
                                'ollama_cloud_models_tested':ollama_cloud_models,
                                'api_cloud_models_tested':api_cloud_models,
                                'single_reviewer_by_author':{case['author_model']:case['review_model']
                                    for case in briefs if case['review_model']},
                                'standalone_review_role':'four criteria; qualified source/arbiter family separate from editorial families; author excluded',
                                'reviewer_capability_pool':reviewer_pool,
                                'reviewer_qualifications':reviewer_qualifications,
                                'per_author_case_budget_seconds':AUTHOR_CASE_BUDGET_SECONDS,
                                'per_model_call_budget_seconds':AUTHOR_CALL_BUDGET_SECONDS,
                                'author_work_reserve_for_review_seconds':REVIEWER_RESERVE_SECONDS,
                                'primary_reviewer_backup_reserve_seconds':REVIEWER_BACKUP_RESERVE_SECONDS,
                                'whole_task_retry_budget':2 if profile=='full' else 1,
                                'execution_mode':'full_delivery' if profile=='full' else 'bounded_workflow',
                                'maximum_targeted_final_repairs':2 if profile=='full' else 1,
                                'first_pass_reported_separately':True,
                                'retry_local_model_timeout':False,
                                'abort_case_on_first_timeout':True,
                                'workflow_types_tested':sorted({case['workflow_type'] for case in briefs}),
                                'randomized_task_fields':['business','audience','goal','channel','cadence','brief_formulation','workflow_type'],
                                'replay_env_var':'AI_SMM_ACCEPTANCE_SEED'},
             'cases':[]}
    progress_path=target/'real_workflow_progress.jsonl'
    checkpoint_path=target/'real_workflow_checkpoint.json'
    progress_path.write_text('',encoding='utf-8')
    results['status']='RUNNING'
    results['current_case']=None
    def save_checkpoint():
        checkpoint_path.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    save_checkpoint()
    try:
        if profile=='fast':
            recovery=[]
            (target/'strategy_recovery_results.json').write_text(json.dumps({'status':'NOT_RUN','reason':'fast profile'},ensure_ascii=False,indent=2),encoding='utf-8')
        else:
            recovery=run_recovery_cases(target)
    except Exception as exc:
        recovery=[{'name':'recovery_suite','status':'FAIL','error':type(exc).__name__+': '+str(exc)}]
        (target/'strategy_recovery_results.json').write_text(json.dumps(recovery,ensure_ascii=False,indent=2),encoding='utf-8')
    results['recovery_cases']=[{k:v for k,v in item.items() if k not in ('content','events')} for item in recovery]
    failures=sum(item.get('status')!='PASS' for item in recovery)
    for case in briefs:
        root=target/case['name']
        root.mkdir(exist_ok=True)
        case_started=time.monotonic()
        case_budget_state={'expired':False}
        request=case['request']
        initial_response=None
        progress=[]
        results['current_case']={'name':case['name'],'author_model':case['author_model'],
                                 'review_model':case['review_model'],'started':datetime.now(timezone.utc).isoformat()}
        save_checkpoint()
        def record_progress(item=None, **details):
            item=normalize_progress_event(item,**details)
            progress.append(item)
            if not isinstance(item,dict): return
            safe_keys=('stage','stage_name','attempt','duration_s','outcome','error_type',
                       'reason','batch','batches','segments','business_claims','window',
                       'windows','status','issue_count','completed','total','publication',
                       'author_model','reviewer_model','primary_model','backup_model','provider','model',
                       'from_model','to_model','reserve_seconds','dimension')
            safe={key:item[key] for key in safe_keys if key in item}
            with progress_path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'case':case['name'],'author_model':case['author_model'],
                    'at':datetime.now(timezone.utc).isoformat(),'event':safe},ensure_ascii=False)+'\n')
                stream.flush()
        try:
            context={'project':case['name'], 'project_workspace':str(root),
                     'category':case['category'], 'description':request,
                     'existing_documents':[], 'recent_messages':[],
                     'randomized_acceptance_run':True,
                     'acceptance_profile':os.environ.get('AI_SMM_TEST_PROFILE','full').strip().casefold(),
                     'acceptance_execution_mode':('full_delivery' if profile=='full' else 'bounded_workflow')}
            from ollama_resilience import request_deadline
            with acceptance_model_pool(reviewer_pool), request_deadline(AUTHOR_CASE_BUDGET_SECONDS,
                                      max_call_seconds=AUTHOR_CALL_BUDGET_SECONDS,
                                      retry_timeouts=False,abort_case_on_timeout=True,
                                      reviewer_reserve_seconds=REVIEWER_RESERVE_SECONDS,
                                      reviewer_backup_reserve_seconds=REVIEWER_BACKUP_RESERVE_SECONDS) as case_budget_state:
                def capture_initial(value):
                    nonlocal initial_response
                    initial_response=value
                response=run_text_task(request,context,model=case['author_model'],
                    review_model=case['review_model'],progress=record_progress,
                    on_initial_response=capture_initial)
                if case['workflow_type']=='compound_strategy_calendar':
                    docs=response.get('documents',[]) if isinstance(response,dict) else []
                    delivery=response.get('content_delivery',{}) if isinstance(response,dict) else {}
                    from content_requirements import publication_scope
                    expected=publication_scope(request).total
                    completed=delivery.get('checked_posts') if isinstance(delivery,dict) else None
                    editor_status=(delivery.get('independent_editor',{}).get('status')
                                   if isinstance(delivery,dict) else 'MISSING')
                    editor_issue_count=(len(delivery.get('independent_editor',{}).get('issues',[]))
                                        if isinstance(delivery,dict) else None)
                    okay=(bool(docs) and len(docs)==2 and all(d.get('valid') is True for d in docs)
                          and completed==expected and not delivery.get('unfulfilled')
                          and editor_status=='PASS' and not case_budget_state.get('expired'))
                else:
                    review=response.get('quality_review',{}) if isinstance(response,dict) else {}
                    panel_models=list((review.get('reviewer_models') or {}).values()) if isinstance(review,dict) else []
                    accepted=(isinstance(response,dict) and response.get('action')=='write'
                              and isinstance(response.get('answer'),str) and bool(response['answer'].strip())
                              and isinstance(review,dict) and review.get('pass') is True
                              and review.get('independent_model') is True
                              and _independent_panel(review.get('reviewer_models'))
                              and all(_model_family(name) not in {_model_family(case['author_model']),
                                  _model_family(review.get('revision_author_model') or case['author_model'])} for name in panel_models))
                    docs=[{'kind':'standalone_post','title':'Одиночная публикация',
                           'content':response.get('answer','') if isinstance(response,dict) else '',
                           'valid':accepted,
                           'reason':str(review.get('reason') or '') if isinstance(review,dict) else 'Нет редакторского вердикта'}]
                    delivery={'checked_posts':1 if accepted else 0,
                              'unfulfilled':[] if accepted else ['standalone_post quality contract'],
                              'independent_editor':{'status':'PASS' if accepted else 'BLOCKED',
                                  'issues':review.get('issues',[]) if isinstance(review,dict) else []}}
                    expected=1
                    completed=delivery['checked_posts']
                    editor_status=delivery['independent_editor']['status']
                    editor_issue_count=len(delivery['independent_editor']['issues'])
                    okay=accepted and not case_budget_state.get('expired')
            case_duration=round(time.monotonic()-case_started,2)
            case_deadline_exceeded=bool(case_budget_state.get('expired'))
            review=response.get('quality_review',{}) if isinstance(response,dict) else {}
            if case['workflow_type']=='compound_strategy_calendar':
                editor_status=(delivery.get('independent_editor',{}).get('status')
                               if isinstance(delivery,dict) else 'MISSING')
                editor_issue_count=(len(delivery.get('independent_editor',{}).get('issues',[]))
                                    if isinstance(delivery,dict) else None)
            log={'name':case['name'],'status':'PASS' if okay else 'FAIL',
                 **workflow_outcome_metrics(case['workflow_type'],progress,review,
                      execution_mode=context['acceptance_execution_mode']),
                 'author_model':case['author_model'],
                 'review_model':case['review_model'],
                 'workflow_type':case['workflow_type'],
                 'task_profile':{k:case[k] for k in ('workflow_type','category','channel','posts','audience','goal','scenario_id')},
                 'duration_s':case_duration,
                 'case_budget_s':AUTHOR_CASE_BUDGET_SECONDS,
                 'case_deadline_exceeded':case_deadline_exceeded,
                 'case_aborted_after_model_timeout':bool(case_budget_state.get('aborted_on_model_timeout')),
                 'author_budget_exhausted':bool(case_budget_state.get('author_budget_exhausted')),
                 'reviewer_failovers':sum(1 for item in progress if isinstance(item,dict) and
                     item.get('stage') in ('plan_review_failover','independent_reviewer_failover')),
                 'reviewer_timeouts':sum(1 for item in progress if isinstance(item,dict) and
                     item.get('stage')=='reviewer_model_timeout'),
                 'completed':completed,'expected':expected,
                 'independent_editor_status':editor_status,
                 'independent_editor_issue_count':editor_issue_count,
                 'standalone_quality_review':review if case['workflow_type']=='standalone_post' else None,
                 'documents':[{'kind':d.get('kind'),'valid':d.get('valid'),
                               'reason':str(d.get('reason') or '')[:700],
                               'length':len(d.get('content') or '')} for d in docs],
                 'errors':delivery.get('unfulfilled') if isinstance(delivery,dict) else [],
                 'pipeline_events':len(progress),
                 'last_stages':[x.get('stage') for x in progress[-15:] if isinstance(x,dict)]}
            if not okay:failures+=1
            results['cases'].append(log)
            # Save *synthetic* generated documents for independent semantic review.
            (target/(case['name']+'_synthetic_documents.json')).write_text(
                json.dumps({'seed':task_seed,'first_pass_response':initial_response,'author_model':case['author_model'],
                            'review_model':case['review_model'],
                            'workflow_type':case['workflow_type'],
                            'task_profile':{k:case[k] for k in ('workflow_type','category','channel','posts','audience','goal','scenario_id')},
                            'request':request,'response':response},ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception as exc:
            failures+=1
            case_deadline_exceeded=bool(case_budget_state.get('expired'))
            results['cases'].append({'name':case['name'],'status':'FAIL',
               'author_model':case['author_model'],
               'review_model':case['review_model'],
               'workflow_type':case['workflow_type'],
               'task_profile':{k:case[k] for k in ('workflow_type','category','channel','posts','audience','goal','scenario_id')},
               'duration_s':round(time.monotonic()-case_started,2),
               'case_budget_s':AUTHOR_CASE_BUDGET_SECONDS,
               'case_deadline_exceeded':case_deadline_exceeded,
               'case_aborted_after_model_timeout':bool(case_budget_state.get('aborted_on_model_timeout')),
               'author_budget_exhausted':bool(case_budget_state.get('author_budget_exhausted')),
               'error':type(exc).__name__+': '+str(exc)[:500],
               'traceback':traceback.format_exc()[-1800:]})
        finally:
            draft_files={}
            for file in sorted((root/'drafts').glob('*')):
                if file.is_file() and file.suffix in ('.json','.jsonl'):
                    draft_files[file.name]=file.read_text(encoding='utf-8')
            (target/(case['name']+'_workflow_evidence.json')).write_text(
                json.dumps({'events':progress,'drafts':draft_files},ensure_ascii=False,indent=2),encoding='utf-8')
            results['current_case']=None
            save_checkpoint()
    results['status']='PASS' if failures==0 else 'FAIL'
    results['model_comparison']=[{
        'model':model,
        'cases':[{'name':item.get('name'),'status':item.get('status'),
                  'workflow_type':item.get('workflow_type'),
                  'review_model':item.get('review_model'),
                  'duration_s':item.get('duration_s'),
                  'case_budget_s':item.get('case_budget_s'),
                  'case_deadline_exceeded':item.get('case_deadline_exceeded'),
                  'completed':item.get('completed'),'expected':item.get('expected'),
                  'independent_editor_status':item.get('independent_editor_status'),
                  'independent_editor_issue_count':item.get('independent_editor_issue_count'),
                  'documents':item.get('documents',[]), 'error':item.get('error')}
                 for item in results['cases'] if item.get('author_model')==model],
    } for model in author_models]
    (target/'real_workflow_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False,indent=2),flush=True)
    return 0 if failures==0 else 2

if __name__=='__main__':
    sys.exit(run())
