"""Compound document task executor: no writes from worker thread.

Returns drafts to the UI; UI validates and persists each document independently.
"""
import json
import hashlib
from pathlib import Path
import re
import unicodedata
import urllib.request
from agent_runtime import validate_document, is_discussion_request
from professional_content import requested_weeks, editorial_audit, week_ranges
from calendar_planner import slots_for_weeks, markdown_table, validate_slots
from publication_writer import write_week, write_single, repair_single, repair_fields, repair_field_targets, format_repair_issues, normalize_model_post, repeated_rejected_claims, validate_single, week_to_markdown
from task_context import extract_facts, missing_essentials, question_for_missing
from content_quality_engine import inspect_post, critical_issues
from editorial_guard import add_production_materials, editorial_risks, copy_too_similar
from editorial_integrity import content_consistency_issues
from semantic_editor import audit_post, audit_post_with_independent_model, REVIEW_VERSION, POLICY_VERSION
from ollama_resilience import request_chat, OllamaStageError
from semantic_brief import analyze, plan_slots, propose_topic_replacement, topic_repeats
from content_requirements import publication_scope, delivery_report
from task_orchestration import TaskLedger
from plan_governance import govern_plan, slot_id
from durable_json import atomic_write_json
from publication_schedule import publication_ids
from strategy_recovery import produce_checked_strategy
from evidence_context import build_evidence_context
from acceptance_policy import is_first_pass_acceptance, attempt_limit, is_targeted_recovery, initial_generation_only
from artifact_review import deduplicate_findings


def _unanswered_critical_questions(questions, original, *, model=None, report=None):
    """AI judges whether each question is already answered by the original brief.

    The verifier must quote evidence verbatim; the host checks it against the
    source. Unverifiable decisions and model failures fail closed. This is a
    semantic decision, NOT a phrase/keyword lookup of audience or services.
    """
    pending=[q.strip() for q in (questions if isinstance(questions,list) else [])
             if isinstance(q,str) and q.strip()]
    if not pending:
        return []
    if not model:
        return pending  # Never silently waive clarification without a verifier.
    source=str(original or '')
    system=(
        'You verify whether requested clarifications have ALREADY been answered '
        'in a user brief. Return exactly one JSON object with a `decisions` list '
        'of the same length and order as questions. Each item has `answered` '
        '(boolean) and `evidence_quote` (string). `answered=true` only if the '
        'brief unambiguously provides the full answer, not a placeholder, '
        'speculation, or a partial answer to a compound question. Quote the '
        'smallest exact passage from the user brief supporting the full answer. '
        'If missing, ambiguous or incomplete: answered=false, evidence_quote="". '
        'Do not infer business facts and do not answer questions yourself.'
    )
    try:
        raw=_ask(model,system,json.dumps({'brief':source,'questions':pending},ensure_ascii=False),
                 stage='проверка необходимости уточнений',report=report)
        decision=json.loads(raw)
        rows=decision.get('decisions') if isinstance(decision,dict) else None
        if not isinstance(rows,list) or len(rows)!=len(pending):
            raise ValueError('clarification verification list length mismatch')
        unresolved=[]
        for question,row in zip(pending,rows):
            if not isinstance(row,dict) or type(row.get('answered')) is not bool:
                unresolved.append(question);continue
            quote=row.get('evidence_quote')
            if not (row['answered'] and isinstance(quote,str) and len(quote.strip())>=3
                    and quote.strip() in source):
                unresolved.append(question);continue
            # A verbatim quote proves provenance, not relevance. Require a
            # separate semantic challenge to affirm that the quoted passage
            # answers this PARTICULAR question in full. Fail closed.
            challenge_system=(
                'Independently audit whether the evidence answers the question. '
                'Return exactly one JSON object: {"sufficient": true or false}. '
                'Answer false when the quote is unrelated, partial, vague, '
                'a placeholder, or requires inference. Never add missing facts.'
            )
            try:
                challenge_raw=_ask(model,challenge_system,json.dumps({
                    'question':question,'brief':source,'evidence_quote':quote.strip()},
                    ensure_ascii=False),stage='независимая проверка цитаты',report=report)
                challenge=json.loads(challenge_raw)
                if not (isinstance(challenge,dict)
                        and type(challenge.get('sufficient')) is bool
                        and challenge['sufficient'] is True):
                    unresolved.append(question)
            except (ValueError,TypeError,KeyError,RuntimeError,OllamaStageError) as exc:
                if callable(report):
                    report('clarification_evidence_challenge_failed',error=str(exc)[:200])
                unresolved.append(question)
        return unresolved
    except (ValueError,TypeError,KeyError,RuntimeError,OllamaStageError) as exc:
        if callable(report):
            report('clarification_verification_unavailable',error=str(exc)[:200])
        return pending

def is_compound_request(message):
    # Detect the requested artifact pair from the user's nouns, not from one
    # imperative verb. Randomized and natural-language briefs can say
    # "мне нужна стратегия и контент-план" or contain minor grammatical
    # variation while still unambiguously requesting both documents.
    if is_discussion_request(message):
        return False
    # Normalize separators for routing only; keep the canonical request exact.
    raw=unicodedata.normalize('NFKC',str(message or ''))
    raw=''.join('-' if unicodedata.category(ch)=='Pd' else '' if ch=='\u00ad' else ch for ch in raw)
    s = ' '.join(raw.casefold().split())
    has_strategy = bool(re.search(r'стратег\w*', s, re.I))
    has_content_plan = bool(re.search(r'контент[\s-]*план\w*', s, re.I))
    return has_strategy and has_content_plan


def _ask(model, system, user, *, stage='генерация публикаций', report=None, json_schema=None):
    payload = {'model':model,'stream':False,'think':False,
        'messages':[{'role':'system','content':system},{'role':'user','content':user}],
        'format':json_schema if json_schema is not None else ('json' if 'JSON' in system else None),
        'options':{'num_ctx':8192,'temperature':0.55 if stage=='планирование тем' else 0.25}}
    data=request_chat(payload,stage=stage,timeout=300,attempts=2,report=report)
    return str(data.get('message',{}).get('content') or '').strip()


def _terminal_acceptance_budget_error(exc):
    """Recognize exhausted acceptance-case budgets that cannot recover by retrying."""
    message=str(exc)
    return any(marker in message for marker in (
        'author budget ended to preserve independent-review time',
        'acceptance case stopped after a model-call timeout',
        'acceptance author-case time budget exceeded'))


_STRATEGY_DRAFT_SCHEMA = {
    # This schema is a generation hint. The host-side compiler below accepts
    # common equivalent JSON shapes from different model families and renders
    # one canonical document contract. Model-specific extra fields must not
    # make an otherwise complete strategy unusable.
    'type':'object','additionalProperties':True,
    'required':['objectives','audience','strategy_direction','channel_plan',
                'content_pillars','metrics','implementation_steps'],
    'properties':{
        'objectives':{'type':'array','minItems':1,'maxItems':5,'items':{'type':'string'}},
        'audience':{'type':'string'},
        'strategy_direction':{'type':'string'},
        'channel_plan':{'type':'string'},
        'content_pillars':{'type':'array','minItems':1,'maxItems':6,'items':{'type':'string'}},
        'metrics':{'type':'array','minItems':1,'maxItems':6,'items':{
            'type':'object','additionalProperties':False,'required':['name','method'],
            'properties':{'name':{'type':'string'},'method':{'type':'string'}}}},
        'implementation_steps':{'type':'array','minItems':1,'maxItems':7,'items':{'type':'string'}},
        'risks_unknowns':{'type':'array','minItems':1,'maxItems':6,'items':{'type':'string'}},
    }
}


def _strategy_text(value, *, preferred=()):
    """Extract human-readable content from common JSON field representations."""
    if isinstance(value,str):
        return value.strip()
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        return str(value)
    if isinstance(value,list):
        parts=[_strategy_text(item,preferred=preferred) for item in value]
        return '\n'.join(part for part in parts if part)
    if isinstance(value,dict):
        keys=(*preferred,'name','title','label','metric','indicator','goal','objective',
              'segment','pillar','topic','platform','channel','approach','description',
              'needs','need','method','measurement','measurement_method','rationale',
              'action','step','risk','unknown','value')
        parts=[]
        for key in keys:
            item=value.get(key)
            if item is not None:
                rendered=_strategy_text(item)
                if rendered and rendered not in parts:
                    parts.append(rendered)
        if parts:
            return ' — '.join(parts)
        # Provider output sometimes uses the platform name as a dynamic key,
        # e.g. {"instagram": "..."}. Preserve that meaning for the compiler
        # instead of converting the whole channel plan into an empty string.
        return '\n'.join(f'{key}: {_strategy_text(item)}' for key,item in value.items()
                         if _strategy_text(item))
    return ''


def _strategy_field(obj, *names):
    for name in names:
        value=obj.get(name)
        if value is not None and value != '':
            return value
    return None


def _strategy_list(value, name, *, minimum=1):
    """Normalize scalar/list/object variants without inventing content."""
    if isinstance(value,list):
        items=value
    elif isinstance(value,dict):
        # A mapping may be one metric/segment or a named collection.
        if any(key in value for key in ('name','metric','indicator','segment','pillar','title','goal','objective')):
            items=[value]
        else:
            items=list(value.values())
    elif value is None:
        items=[]
    else:
        items=[value]
    result=[]
    for item in items:
        rendered=_strategy_text(item)
        if rendered and rendered not in result:
            result.append(rendered)
    if len(result)<minimum:
        raise ValueError(f'Недостаточно содержания в разделе стратегии: {name}')
    return result


def _strategy_metrics(value):
    """Canonicalize metric objects from common provider naming conventions."""
    if isinstance(value,dict):
        if any(key in value for key in ('name','metric','indicator','measurement')):
            rows=[value]
        else:
            rows=[]
            for key,item in value.items():
                if isinstance(item,dict):
                    rows.append({'name':key,**item})
                else:
                    rows.append({'name':key,'method':item})
    elif isinstance(value,list):
        rows=value
    else:
        rows=[] if value is None else [value]
    result=[]
    for row in rows:
        if isinstance(row,dict):
            name=_strategy_text(_strategy_field(row,'name','metric','indicator','kpi','title'))
            method=_strategy_text(_strategy_field(row,'method','measurement_method','measurement','how'))
        else:
            name=_strategy_text(row)
            method=''
        if name and method:
            pair=(name,method)
            if pair not in result:
                result.append(pair)
    if not result:
        raise ValueError('Не заполнены метрики или способы их измерения')
    return result


def _render_strategy_draft(raw, *, channel_context=None):
    """Turn a schema-checked strategy response into a complete, addressable document."""
    import re
    text=str(raw or '').strip()
    text=re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I).strip()
    try:
        obj=json.loads(text)
    except (TypeError,ValueError) as exc:
        raise ValueError('Модель не вернула стратегию в заданной JSON-схеме') from exc
    if not isinstance(obj,dict):
        raise ValueError('Структура стратегии не соответствует обязательным разделам')
    # This boundary validates shape and usable text. Editorial usefulness is
    # evaluated by the independent quality gates below; arbitrary length floors
    # rejected concise, valid local-model fields and triggered expensive full
    # strategy recovery cycles (A75 R19 evidence).
    def string(value,name):
        if not isinstance(value,str) or not value.strip():
            raise ValueError(f'Недостаточно содержания в разделе стратегии: {name}')
        return value.strip()
    objectives=_strategy_list(_strategy_field(obj,'objectives','goals','objectives_and_goals'),
                              'цели',minimum=1)
    audience=string(_strategy_text(_strategy_field(obj,'audience','target_audience','segments'),
                                   preferred=('segment','description','needs')),'аудитория')
    direction=string(_strategy_text(_strategy_field(obj,'strategy_direction','strategic_direction',
                                   'positioning','strategic_approach'),
                                   preferred=('direction','approach','description')),'направление')
    raw_channel=_strategy_field(obj,'channel_plan','channels','distribution_plan','channel_strategy')
    raw_channel=_strategy_text(raw_channel,preferred=('platform','channel','approach','rationale'))
    # The compiler owns shape and labels, not strategic content. A bare
    # platform name is preserved for independent usefulness review rather
    # than expanded into a host-authored plan or rejected by character count.
    from platform_registry import canonical_platform_name
    channel=string(canonical_platform_name(raw_channel),'канал и продвижение')
    pillars=_strategy_list(_strategy_field(obj,'content_pillars','pillars','content_directions'),
                           'контентные направления',minimum=1)
    steps=_strategy_list(_strategy_field(obj,'implementation_steps','actions','action_plan','plan_steps'),
                         'план действий',minimum=1)
    risks_value=_strategy_field(obj,'risks_unknowns','risks','unknowns','assumptions')
    risks=_strategy_list(risks_value,'риски и неизвестные данные',minimum=0)
    metrics=_strategy_metrics(_strategy_field(obj,'metrics','kpis','measurement'))
    lines=['# Стратегия SMM','', '## Цели и задачи']
    lines.extend(f'- {item}' for item in objectives)
    lines.extend(['','## Целевая аудитория',audience,'',
                  '## Стратегическое направление',direction,'',
                  '## Канал и продвижение',channel,'',
                  '## Контентные направления'])
    lines.extend(f'- {item}' for item in pillars)
    lines.extend(['','## Метрики и измерение'])
    lines.extend(f"- **{name}** — {method}" for name,method in metrics)
    lines.extend(['','## План действий'])
    lines.extend(f'{index}. {item}' for index,item in enumerate(steps,1))
    if risks:
        lines.extend(['','## Риски и данные для уточнения'])
        lines.extend(f'- {item}' for item in risks)
    return '\n'.join(lines).strip()


def _decode_strategy_proposal(raw, *, channel_context=None):
    """One compiler for generation, rejected checkpoints and edit proposals."""
    if not isinstance(raw, str):
        raise TypeError('Предложение документа должно быть строкой')
    try:
        return _render_strategy_draft(raw, channel_context=channel_context)
    except ValueError as exc:
        # Existing prose/Markdown adapters remain subject to their ordinary
        # structural and independent gates. Recognized JSON, including damaged
        # JSON, keeps its exact bytes and decoder failure as a typed rejection.
        probe=re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.I).strip()
        try:
            json.loads(probe)
        except ValueError:
            if not probe.startswith(('{','[')):
                return raw
        from document_recovery import GeneratedDocument
        return GeneratedDocument(raw, str(exc))


def execute_compound(message, context, model='qwen3:8b', progress=None):
    # Routing is decided once by the UI; never reinterpret a clarified/new task.
    original = str(message)
    task_mode = context.get('task_mode', 'new')
    resume_mode = task_mode in ('resume','final_review_repair','revision')
    first_pass_acceptance=is_first_pass_acceptance(context)
    # Create a task-scoped diagnostic trace BEFORE semantic analysis and planning.
    # Earlier stages created diagnostics only after these expensive failure-prone calls.
    import time
    from datetime import datetime, timezone
    started_at = time.monotonic()
    workspace = context.get('project_workspace')
    try:
        scope = publication_scope(original)
    except ValueError as exc:
        return {'action':'compound_documents','instruction':original,'documents':[],
                'error':'Противоречивые требования к объёму контента: '+str(exc)}
    weeks, per_week = scope.weeks, scope.per_week
    # Preserve existing valid checkpoints for the historical 3/week format.
    # Other frequencies get distinct signatures so they cannot reuse old slots.
    sig_source = str(workspace)+'\n'+original+'\n'+str(weeks)
    if per_week != 3:
        sig_source += '\n'+str(per_week)
    # Two distinct tasks in the same project may contain byte-identical text.
    # Their recovery files, publication ledgers and editor drafts MUST NOT
    # overlap. Preserve old signatures for pre-registry legacy assignments.
    durable_task_id=context.get('task_id')
    if durable_task_id is not None:
        if (not isinstance(durable_task_id,str)
                or not re.fullmatch(r'[0-9a-f]{32}',durable_task_id)):
            raise ValueError('Недопустимый идентификатор задания')
        sig_source+='\ntask_id:'+durable_task_id
    revision_id=context.get('revision_id')
    revision_seed=context.get('revision_seed')
    if revision_id:
        if (not isinstance(revision_id,str) or not re.fullmatch(r'[0-9a-f]{32}',revision_id)
                or not isinstance(revision_seed,dict) or revision_seed.get('request')!=original):
            raise ValueError('Редакция не привязана к исходному заданию')
        sig_source+='\nrevision:'+revision_id
    signature = hashlib.sha256(sig_source.encode('utf-8')).hexdigest()
    recovery=context.get('workflow_recovery')
    if not (task_mode=='final_review_repair' and isinstance(recovery,dict)
            and recovery.get('task_signature')==signature
            and recovery.get('task_contract_signature')==context.get('text_task_signature')):
        recovery={}
    drafts = Path(workspace) / 'drafts' if workspace else None
    # The full task identity is mandatory for all new persistent streams.
    # Legacy prefix files are read-only and must match the exact signature.
    log_path = drafts / ('publication_diagnostics_'+signature+'.jsonl') if drafts else None
    progress_file = drafts / ('publication_progress_'+signature+'.json') if drafts else None
    legacy_progress_file = drafts / ('publication_progress_'+signature[:16]+'.json') if drafts else None
    if drafts:
        drafts.mkdir(parents=True, exist_ok=True)
    # Stable artifact identities and task-level lifecycle are owned by the
    # application, never by the model or the formatting of its reply.
    required_ids=publication_ids(weeks,per_week)
    ledger=TaskLedger.open(drafts,signature)
    ledger.register('document',['strategy','calendar'])
    ledger.register('publication',required_ids)
    docs=[]
    strategy_review_unavailable=False
    def event(stage, **extra):
        nonlocal strategy_review_unavailable
        if stage=='strategy_review_unavailable':
            strategy_review_unavailable=True
        payload = {'time':datetime.now(timezone.utc).isoformat(), 'stage':stage,
                   'elapsed_s':round(time.monotonic()-started_at,1), **extra}
        if log_path:
            try:
                with log_path.open('a',encoding='utf-8') as h:
                    h.write(json.dumps(payload,ensure_ascii=False)+'\n')
            except OSError:
                pass
        if progress:
            try: progress(payload)
            except Exception: pass
    from document_recovery import DocumentDraft
    def publication_slot(ident):
        # The canonical slot ID derives from the host's required assignment,
        # never from a model-generated post or arbitrary filesystem component.
        return DocumentDraft(drafts, signature, 'publication_'+str(required_ids.index(ident)))

    def rejected_publication_slot(ident):
        # Failed revisions belong to a distinct durable stream. Rejection must
        # never downgrade an independently committed accepted candidate.
        return DocumentDraft(drafts, signature, 'publication_rejected_'+str(required_ids.index(ident)))

    def store_accepted_slots():
        if not drafts: return
        for ident, post in accepted_posts.items():
            if ident not in required_ids or not isinstance(post,dict):
                raise ValueError('Cannot checkpoint an unknown or malformed publication')
            publication_slot(ident).save(json.dumps(post,ensure_ascii=False,sort_keys=True), True,
                                         'pre-final-review verified candidate')

    def checkpoint():
        if not progress_file: return
        payload = {'signature':signature,'posts':accepted_posts,'total':scope.total,
                   'completed':len(accepted_posts),
                   'updated_at':datetime.now(timezone.utc).isoformat()}
        store_accepted_slots()
        atomic_write_json(progress_file,payload)
        ledger.reconcile(accepted_posts, strategy=bool(docs and docs[0].get('valid')),
                         calendar=False)
    accepted_posts = {}
    progress_source = progress_file if progress_file and progress_file.is_file() else (
        legacy_progress_file if legacy_progress_file and legacy_progress_file.is_file() else None)
    if progress_source:
        try:
            # Reject ambiguous duplicate JSON keys before recovery.
            def reject_duplicate_keys(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('Duplicate key in publication checkpoint: '+key)
                    result[key] = value
                return result
            data=json.loads(progress_source.read_text(encoding='utf-8'),
                            object_pairs_hook=reject_duplicate_keys)
            if isinstance(data,dict) and data.get('signature') == signature and isinstance(data.get('posts'),dict):
                valid_posts={}
                invalid_posts={}
                fields=('id','topic','caption','visual','cta','kpi','audience','measurement','materials')
                for ident,post in data['posts'].items():
                    if (ident in required_ids and isinstance(post,dict)
                            and post.get('id') == ident
                            and all(isinstance(post.get(f),str) and post[f].strip() for f in fields)):
                        valid_posts[ident]=post
                    else:
                        invalid_posts[ident]=post
                if invalid_posts:
                    # Never delete the only copy of a malformed checkpoint.
                    # Preserve all original bytes under a content-addressed name
                    # before writing a repaired checkpoint with valid entries.
                    original_bytes=progress_source.read_bytes()
                    fingerprint=hashlib.sha256(original_bytes).hexdigest()[:12]
                    quarantine=progress_source.with_name(progress_source.stem+'.'+fingerprint+'.quarantine.json')
                    if not quarantine.exists():
                        try:
                            quarantine.write_bytes(original_bytes)
                        except OSError as exc:
                            return {'action':'compound_documents','instruction':original,'documents':[],
                                    'error':'Нельзя сохранить повреждённую контрольную точку: '+str(exc)}
                    event('checkpoint_entries_quarantined',count=len(invalid_posts),
                          quarantine_file=quarantine.name)
                accepted_posts=valid_posts
        except (OSError, ValueError, TypeError):
            event('checkpoint_read_error')
    # A final-review revocation is an intent committed before individual slots
    # and the aggregate are rewritten. After a crash, this intent has priority
    # over any older accepted checkpoint; otherwise a rejected post reappears.
    revocation_file = drafts / ('final_review_revocations_'+signature+'.json') if drafts else None
    pending_revocations = {}
    if revocation_file and revocation_file.exists():
        def _strict_revocation_object(pairs):
            obj = {}
            for k, v in pairs:
                if k in obj:
                    raise ValueError('Duplicate field in revocation journal')
                obj[k] = v
            return obj
        journal = json.loads(revocation_file.read_text(encoding='utf-8'),
                             object_pairs_hook=_strict_revocation_object)
        if (type(journal) is not dict or type(journal.get('schema')) is not int or journal['schema'] != 1
                or journal.get('signature') != signature
                or type(journal.get('posts')) is not dict
                or any(ident not in required_ids or not isinstance(post, dict)
                       for ident, post in journal['posts'].items())):
            raise ValueError('Untrusted final-review revocation journal')
        pending_revocations = journal['posts']
        for ident in pending_revocations:
            accepted_posts.pop(ident, None)
    # Restore independently committed document slots after a torn or damaged
    # aggregate progress write. A slot is a candidate, not a publication receipt:
    # the normal cached-post gate rechecks policy, scope and editorial version.
    if drafts:
        fields=('id','topic','caption','visual','cta','kpi','audience','measurement','materials')
        for ident in required_ids:
            slot_checkpoint=publication_slot(ident).load()
            if ident in pending_revocations:
                continue  # A durable rejection outranks an older accepted slot.
            if ident in accepted_posts:
                # The aggregate and per-document checkpoint are independent
                # crash-recovery witnesses. Never silently prefer either one
                # if both claim to hold an accepted version but disagree.
                if slot_checkpoint is not None and slot_checkpoint['valid'] is True:
                    aggregate_bytes=json.dumps(accepted_posts[ident],ensure_ascii=False,sort_keys=True)
                    if slot_checkpoint['content'] != aggregate_bytes:
                        raise ValueError('Conflicting accepted publication checkpoints: '+ident)
                continue
            if slot_checkpoint is None or slot_checkpoint['valid'] is not True:
                continue
            try:
                restored=json.loads(slot_checkpoint['content'])
            except (TypeError, ValueError) as exc:
                raise ValueError('Committed publication slot has invalid JSON: '+ident) from exc
            if (not isinstance(restored,dict) or restored.get('id')!=ident
                    or not all(isinstance(restored.get(f),str) and restored[f].strip() for f in fields)):
                raise ValueError('Committed publication slot contradicts its assignment: '+ident)
            accepted_posts[ident]=restored
            event('publication_slot_restored',publication=ident)
    # Rejected text is NOT an accepted checkpoint. Preserve it separately so
    # subsequent runs can revise the actual failed draft, not restart blindly.
    # A rejected-draft stream belongs to the full immutable assignment identity.
    # Legacy prefix-addressed files are read only and never overwritten; two
    # assignments sharing a prefix must not overwrite one another's revisions.
    repair_file = drafts / ('publication_repair_'+hashlib.sha256(signature.encode('utf-8')).hexdigest()+'.json') if drafts else None
    legacy_repair_file = drafts / ('publication_repair_'+signature[:16]+'.json') if drafts else None
    repair_drafts = {}
    for ident, post in pending_revocations.items():
        repair_drafts[ident] = {'post':post,'issues':[],
                                'review_state':'unavailable',
                                'revoked_by_final_review':True}
    if isinstance(recovery.get('rejected_posts'),dict):
        import copy
        repair_drafts=copy.deepcopy(recovery['rejected_posts'])
    repair_source = repair_file if repair_file and repair_file.is_file() else (
        legacy_repair_file if legacy_repair_file and legacy_repair_file.is_file() else None)
    if repair_source:
        try:
            cached_repair = json.loads(repair_source.read_text(encoding='utf-8'))
            if (isinstance(cached_repair,dict) and cached_repair.get('signature') == signature and
                    isinstance(cached_repair.get('drafts'), dict)):
                repair_drafts = cached_repair['drafts']
        except (OSError, ValueError, TypeError):
            event('repair_checkpoint_read_error')
    for ident, post in pending_revocations.items():
        previous = repair_drafts.get(ident)
        retained_issues = (previous.get('issues', [])
                           if isinstance(previous, dict) and isinstance(previous.get('issues'), list)
                           else [])
        # Crash intent is newer than the repair journal. Do not
        # replay a stale draft for the same publication.
        repair_drafts[ident] = {'post':post, 'issues':retained_issues,
            'review_state':'unavailable', 'revoked_by_final_review':True}
    def repair_checkpoint():
        if not repair_file: return
        # Preserve rejected bytes separately from the accepted aggregate.  A
        # rejected slot is never eligible for replay as a verified candidate.
        for ident, rejection in repair_drafts.items():
            if ident not in required_ids or not isinstance(rejection,dict):
                continue
            post=rejection.get('post')
            if isinstance(post,dict):
                rejected_publication_slot(ident).save(
                    json.dumps(post,ensure_ascii=False,sort_keys=True),False,
                    json.dumps(rejection.get('issues',[]),ensure_ascii=False)[:1800])
        payload = {'signature':signature,'drafts':repair_drafts,
                   'updated_at':datetime.now(timezone.utc).isoformat()}
        atomic_write_json(repair_file,payload)
    if revision_id and revision_seed and repair_file and not repair_file.exists():
        repair_drafts={ident:{'post':dict(post),'issues':[],'rejected_captions':[],
            'review_state':'unavailable'} for ident,post in revision_seed.get('posts',{}).items()
            if isinstance(post,dict)}
        repair_checkpoint()
        event('explicit_revision_seeded',publications=len(repair_drafts),original_saved_documents_preserved=True)
    if pending_revocations:
        repair_checkpoint()
        for ident, post in pending_revocations.items():
            publication_slot(ident).save(
                json.dumps(post, ensure_ascii=False, sort_keys=True), False,
                'revoked by recovered final-review intent')
        checkpoint()
        revocation_file.unlink()
        event('final_review_revocations_replayed', count=len(pending_revocations))
    final_review_repair_slots=set(context.get('final_review_repair_slots') or [])
    final_review_repair_slots &= set(required_ids)
    final_review_issues_by_slot=context.get('final_review_issues_by_slot') or {}
    if final_review_repair_slots:
        # A final-review rejection applies to unapproved drafts only. Preserve
        # the exact pre-repair checkpoint under a content-addressed name before
        # reopening only the cited slots for targeted correction.
        try:
            # Back up the checkpoint actually used for recovery, including a
            # legacy read-only source. A migration must never lose the only
            # pre-repair bytes when the canonical path does not yet exist.
            if progress_source and progress_source.is_file():
                raw_checkpoint=progress_source.read_bytes()
                checkpoint_hash=hashlib.sha256(raw_checkpoint).hexdigest()[:12]
                backup=progress_file.with_name(
                    progress_file.stem+'.'+checkpoint_hash+'.final_review_backup.json')
                if not backup.exists():
                    backup.write_bytes(raw_checkpoint)
            if revocation_file:
                to_revoke = {}
                for ident in sorted(final_review_repair_slots):
                    post = accepted_posts.get(ident)
                    if post is None:
                        slot = publication_slot(ident).load()
                        if slot is not None and slot['valid'] is True:
                            post = json.loads(slot['content'])
                    if isinstance(post, dict):
                        to_revoke[ident] = post
                if to_revoke:
                    atomic_write_json(revocation_file, {'schema':1,
                        'signature':signature, 'posts':to_revoke})
            for ident in sorted(final_review_repair_slots):
                rejected=accepted_posts.pop(ident,None)
                if isinstance(rejected,dict):
                    issues=final_review_issues_by_slot.get(ident,context.get('final_review_issues',[]))
                    repair_drafts[ident]={'post':rejected,
                        # Keep the full finding (exact claim, host evidence ID,
                        # field and section) until the publication writer turns
                        # it into repair instructions. Reducing this object to
                        # its reason loses the text the model must remove and
                        # causes repeat rejection of the unchanged claim.
                        'issues':[dict(i) if isinstance(i,dict) else str(i)
                                  for i in issues]}
            repair_checkpoint()
            # A reopened draft is no longer an accepted candidate. Persist
            # revocation in both durable witnesses before resuming the author.
            # Otherwise restart rehydrates it from the old per-slot checkpoint.
            for ident in sorted(final_review_repair_slots):
                prior=repair_drafts.get(ident,{}).get('post')
                if isinstance(prior,dict) and drafts:
                    publication_slot(ident).save(
                        json.dumps(prior,ensure_ascii=False,sort_keys=True), False,
                        'revoked by final independent review')
            checkpoint()
            if revocation_file and revocation_file.exists():
                revocation_file.unlink()
            event('final_review_drafts_reopened',slots=sorted(final_review_repair_slots),
                  preserved_existing_checkpoints=True)
        except OSError as exc:
            return {'action':'compound_documents','instruction':original,'documents':[],
                    'error':'Нельзя сохранить черновик перед итоговой доработкой: '+str(exc)[:240]}
    prior_rejections = set(repair_drafts)
    def remember_rejection(ident, post, problems, *, preserve_existing=False):
        if (preserve_existing and ident in prior_rejections
                and isinstance(repair_drafts.get(ident),dict)
                and isinstance(repair_drafts[ident].get('post'),dict)):
            if ident in unavailable_publications:
                repair_drafts[ident]['review_state']='unavailable'
                repair_checkpoint()
            return  # A failed weekly batch must not erase last run's repair draft.
        if isinstance(post, dict):
            old = repair_drafts.get(ident, {})
            history = list(old.get('rejected_captions', [])) if isinstance(old, dict) else []
            old_issues = list(old.get('issues', [])) if isinstance(old, dict) else []
            new_issues = [dict(x) if isinstance(x,dict) else str(x) for x in problems]
            # Typed final-editor findings carry exact quotes, evidence IDs and
            # field roles. A later local string warning must not erase them.
            if any(isinstance(x,dict) for x in old_issues) and not any(isinstance(x,dict) for x in new_issues):
                saved_issues = old_issues + [x for x in new_issues if x not in old_issues]
            else:
                saved_issues = new_issues
            caption = str(post.get('caption') or '').strip()
            if caption and caption not in history:
                history.append(caption)
            repair_drafts[ident] = {'post':post, 'issues':saved_issues,
                                    'rejected_captions':history[-4:],
                                    'review_state':'unavailable' if ident in unavailable_publications else 'rejected'}
            repair_checkpoint()
    def forget_rejection(ident):
        if repair_drafts.pop(ident, None) is not None:
            repair_checkpoint()
    plan_file = drafts / ('topic_plan_'+signature[:16]+'.json') if drafts else None
    cached_slots = []
    if isinstance(recovery.get('slots'),list):
        import copy
        cached_slots=copy.deepcopy(recovery['slots'])
    if plan_file and plan_file.is_file():
        try:
            payload=json.loads(plan_file.read_text(encoding='utf-8'))
            if isinstance(payload,dict) and payload.get('signature') == signature and isinstance(payload.get('slots'),list):
                cached_slots=payload['slots']
        except (OSError, ValueError, TypeError):
            event('topic_checkpoint_read_error')
    if revision_id and not cached_slots:
        cached_slots=[dict(slot) for slot in revision_seed.get('slots',[]) if isinstance(slot,dict)]
    def save_plan(week, slots):
        if plan_file:
            payload={'signature':signature,'slots':slots,'updated_at':datetime.now(timezone.utc).isoformat()}
            atomic_write_json(plan_file,payload)
        event('topic_week_accepted',week=week,planned=len(slots),total=scope.total)
    checkpoint()
    event('task_started' , total=scope.total, resumed=len(accepted_posts))
    facts={'project':context.get('project'), 'category':context.get('category'),
           'description':context.get('description'),
           'existing_documents':context.get('existing_documents',[]),
           'memory_policy':context.get('memory_policy',''),
           'clarification_answer':context.get('clarification_answer'),
           'recent_messages':context.get('recent_messages',[])[-10:] if resume_mode else []}
    evidence_context=build_evidence_context(original,context)
    facts['evidence_context']=evidence_context.to_dict()
    base=json.dumps(facts,ensure_ascii=False)
    # The complete instruction is analyzed by the model. Missing optional facts
    # never trigger hard-coded business/audience interrogation.
    known = ({} if context.get('semantic_authorized') else
             extract_facts(original, (context.get('task_facts') or {}) if resume_mode else {}))
    try:
        if isinstance(recovery.get('analysis'),dict):
            import copy
            brief=copy.deepcopy(recovery['analysis'])
            event('task_analysis_reused',task_signature=signature)
        else:
            brief = analyze(lambda m,sys,usr: _ask(m,sys,usr,stage='анализ полного запроса',report=event),
                            model,original,base)
    except Exception as exc:
        event('task_failed',step='semantic_analysis',error=str(exc)[:400])
        from provider_errors import AuthorExecutionAborted
        return {'action':'compound_documents','instruction':original,'documents':[],
                'error':'Не удалось проанализировать полный запрос: '+str(exc),
                **({'failure_code':'author_execution_aborted'} if isinstance(exc,AuthorExecutionAborted) else {})}
    questions=_unanswered_critical_questions(brief.get('critical_questions'), original, model=model, report=event)
    if questions:
        return {'action':'compound_clarify','instruction':original,'answer':'\n'.join(questions)}
    from platform_registry import canonicalize_platforms, explicit_platforms
    # Platform requirements belong to the request boundary. The language
    # model may summarize the brief, but it cannot replace a channel the user
    # named. This also keeps the rule intact when analysis is resumed or
    # supplied by another provider.
    requested_channels=[] if context.get('semantic_authorized') else explicit_platforms(original)
    if revision_id and cached_slots and not requested_channels:
        # The user selected this saved bundle for revision. Its channel stays
        # execution context, without becoming a claimed business fact.
        brief['channels']=revision_seed.get('platform')
        brief['channels_basis']='explicit'
    if requested_channels:
        brief['channels']=requested_channels
        brief['channels_basis']='explicit'
    channels=canonicalize_platforms(brief.get('channels'))
    if len(channels)!=1:
        if len(channels)>1:
            answer=('В задании указано несколько площадок: '+' и '.join(channels)+'. '
                    'Чтобы не смешивать форматы и не считать публикации неоднозначно, выберите одну площадку '
                    'для этого комплекта либо запустите отдельное задание для каждой площадки.')
        else:
            answer=('Площадка не указана, а независимая рекомендация не получена. '
                    'Укажите социальную сеть или канал, для которого подготовить стратегию и календарь.')
        event('task_clarification_required',field='channels',count=len(channels))
        return {'action':'compound_clarify','instruction':original,'answer':answer}
    brief['channels']=channels[0]
    brief['channels_basis']='explicit' if brief.get('channels_basis')=='explicit' else 'recommendation'
    base += '\nСемантический разбор полного задания: '+json.dumps(brief,ensure_ascii=False)
    # Model-extracted labels remain task-analysis hints; they are not promoted
    # into the factual registry without a source link.
    known['task_analysis']={k:v for k,v in brief.items() if v}
    known['platform']=channels[0]
    known['platform_basis']=brief['channels_basis']
    known['project_memory']=context.get('existing_documents',[])
    known['project_memory_policy']=context.get('memory_policy','')
    facts['evidence_context']=evidence_context.to_dict()
    known['evidence_context']=evidence_context.to_dict()
    base=json.dumps(facts,ensure_ascii=False)+'\nСемантический разбор полного задания: '+json.dumps(brief,ensure_ascii=False)
    # Resume a partially completed task without regenerating an already saved strategy.
    # Only an explicitly accepted project artifact is immutable. A valid
    # strategy carried forward from an earlier pass of the same compound task
    # is still a draft until the complete deliverable passes final review. The
    # latter may be reused as the repair source, but must remain eligible for
    # targeted repair if a later review discovers a strategy finding.
    existing_strategy = next((d.get('content') for d in (context.get('resume_documents', []) if resume_mode else [])
                              if isinstance(d,dict) and d.get('kind') == 'strategy'
                              and d.get('content') and d.get('already_saved') is True), None)
    if existing_strategy is None and recovery:
        # Reuse this execution's unaffected validated draft without pretending
        # it was accepted/saved by the user. A later strategy finding can still reopen it.
        existing_strategy=next((d.get('content') for d in context.get('preserved_stage_documents',[])
            if isinstance(d,dict) and d.get('kind')=='strategy' and d.get('valid') is True
            and isinstance(d.get('content'),str) and d['content'].strip()),None)
    event('strategy_started', reused=bool(existing_strategy))
    from editorial_claim_policy import BUSINESS_FACT_POLICY
    from artifact_review import WRITING_QUALITY_POLICY
    strategy_system = (
        WRITING_QUALITY_POLICY+
        'Ты ведущий SMM-стратег. Пиши на языке исходного запроса, если пользователь явно не попросил другой язык. '
        'Подготовь прикладную верхнеуровневую стратегию. Не включай готовые посты, календарь, '
        'CTA, визуалы или готовые публикационные KPI. Заполни каждый раздел содержательно и '
        'конкретно по запросу; не оставляй пустых или повторных пунктов. Каждая цель и рекомендация '
        'должна прямо вести к запрошенному результату, а не подменять его соседней задачей. Краткие формулировки '
        'разрешены, если они полезны; не растягивай текст ради длины и не дублируй одну мысль. '
        'В objectives опиши цели без выдуманных числовых результатов. Audience передай как '
        'потребности/сегменты из запроса: свяжи каждый значимый сегмент с гипотезой потребности, '
        'сообщением и действием в пределах имеющихся данных. Strategy_direction и content_pillars являются '
        'рекомендациями для тестирования, а не утверждениями о фактических преимуществах бизнеса. '
        'Channel_plan должен объяснить работу с указанным каналом без неподтверждённых заявлений '
        'о его аудитории или эффективности. Для metrics укажи только измеримый показатель и '
        'способ измерения, сопоставимый период наблюдения и правило выбора дальнейших действий; '
        'не выдумывай базу и целевые значения. При заданном бюджете обозначь назначение категорий, '
        'основание предварительного распределения и условия пересмотра расходов. Implementation_steps и '
        'risks_unknowns должны быть применимыми к заданию. Подробный календарь и тексты публикаций '
        'система формирует отдельно после проверки стратегии. '
        'Площадка в задании: '+channels[0]+'. Основание выбора: '
        +('её прямо указал пользователь. Не заменяй её и не добавляй другие площадки. '
          if brief['channels_basis']=='explicit' else
          'это рекомендация по запросу без названной площадки; подпиши её как предложение, а не как выбор пользователя. ')
        +'Название проекта и категорию указывай только если пользователь сообщил их прямо; '
        'не выводи категорию из списка услуг. Для неподтверждённого поля напиши «не указано» '
        'или пропусти его. Используй '
        'task_analysis только для понимания объёма и неизвестных; источником фактов он не является. '
        + BUSINESS_FACT_POLICY +
        ' ОТДЕЛЬНОЕ ПРАВИЛО ДЛЯ ПОЗИЦИОНИРОВАНИЯ: не выводи конкурентное преимущество или '
        'ценностное предложение из желания аудитории. Не обещай идеальную посадку, продление '
        'срока службы, экономию или иной эффект, если он прямо не подтверждён. Не называй '
        'специализацию или услуги сверх точного списка пользователя. Если отличие не подтверждено, '
        'укажи, какие сведения надо уточнить у владельца.'
    )
    def strategy_attempt(previous_failure, attempt):
        retry_note = ('\nПРОШЛАЯ СТРАТЕГИЯ ОТКЛОНЕНА. ПОЛНОСТЬЮ ИСПРАВЬ ЕЁ. '
                      'Не меняй требования пользователя и не добавляй вымышленных сведений. '
                      'Не повторяй указанную как ошибочная фразу даже с небольшой переформулировкой. '
                      'Отдели вопрос/потребность аудитории от того, что компания реально предлагает. '
                      'Причины отказа: ' + previous_failure[:1500]) if previous_failure else ''
        prior = ('\nЗамечания предыдущего выполнения: '
                 + str(context.get('previous_rejection_issues'))[:1000]
                 if context.get('previous_rejection_issues') else '')
        import inspect
        call_kwargs={'stage':('генерация SMM-стратегии' if attempt == 1
                              else 'автоматическое исправление SMM-стратегии'),
                     'report':event}
        try:
            signature_target=getattr(_ask,'side_effect',None)
            if not callable(signature_target):
                signature_target=_ask
            ask_parameters=inspect.signature(signature_target).parameters
            params=ask_parameters.values()
            accepts_schema=('json_schema' in ask_parameters or
                            any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params))
        except (TypeError,ValueError):
            accepts_schema=True
        if accepts_schema:
            call_kwargs['json_schema']=_STRATEGY_DRAFT_SCHEMA
        raw=_ask(model,strategy_system+' Верни только JSON по переданной схеме, без Markdown и пояснений.',
                 'Запрос: '+original+'\nКонтекст: '+base+prior+retry_note,
                 **call_kwargs)
        proposal=_decode_strategy_proposal(raw,channel_context=channels[0])
        from document_recovery import GeneratedDocument
        if isinstance(proposal,GeneratedDocument) and proposal.contract_error:
            event('strategy_structured_response_rejected',attempt=attempt,
                  reason=proposal.contract_error[:220])
        return proposal
    def strategy_repair(rejected_document, failure, attempt):
        # Preserve the exact rejected document as the repair input. This is a
        # document-level transaction, not a fresh regeneration that can drift
        # away from the original brief. The independent gates run afterwards.
        event('strategy_targeted_repair_started', attempt=attempt,
              rejected_length=len(rejected_document),
              issues=[failure[:350]])
        structured_findings=[x for x in context.get('final_review_issues',[])
                             if isinstance(x,dict) and x.get('document')=='strategy']
        if not structured_findings:
            structured_findings=[x for x in context.get('strategy_review_findings',[])
                                 if isinstance(x,dict)]
        if structured_findings:
            event('strategy_repair_findings_attached',attempt=attempt,
                  finding_count=len(structured_findings),
                  evidence_ids=[str(x.get('evidence_id') or '')[:80]
                                for x in structured_findings[:8]])
        if context.get('strict_document_recovery'):
            from document_recovery import patch_document
            try:
                return patch_document(
                    lambda m,s,u,**kwargs: _ask(m,s,u,stage='адресное исправление SMM-стратегии',report=event,**kwargs),
                    model,original,rejected_document,failure,constrained=True,
                    evidence_context=evidence_context,findings=structured_findings)
            except (ValueError,TypeError,KeyError) as patch_error:
                # Keep the same rejected artifact and try a different edit
                # protocol. A malformed/no-op line patch is not permission to
                # discard the durable draft and generate a replacement.
                event('strategy_full_repair_fallback',attempt=attempt,
                      patch_error=str(patch_error)[:240],preserved_rejected_draft=True)
                fallback_system=(strategy_system+' Ты продолжаешь исправление именно этого отклонённого документа. '
                    'Не начинай независимую стратегию и не меняй подтверждённую часть. '
                    'Удали или переформулируй только неподтверждённые утверждения, сохранив структуру и полезное содержание. '
                    'Если процесс неизвестен, обозначь точный вопрос для подтверждения; не придумывай ответ. '
                    'Верни полный исправленный документ в Markdown.')
                return _ask(model,fallback_system,
                    'ИСХОДНОЕ ЗАДАНИЕ (неизменно):\n'+original+
                    '\nОТКЛОНЁННЫЙ ДОКУМЕНТ (его нужно исправить, не заменять новой стратегией):\n'+
                    rejected_document+'\nПРИЧИНЫ ОТКЛОНЕНИЯ:\n'+failure[:1300]+
                    '\nСТРУКТУРИРОВАННЫЕ ПОДТВЕРЖДЁННЫЕ ЗАМЕЧАНИЯ:\n'+json.dumps(structured_findings,ensure_ascii=False)+
                    '\nПРЕДЫДУЩИЙ ПРОТОКОЛ РЕДАКТИРОВАНИЯ НЕ СРАБОТАЛ:\n'+str(patch_error)[:300]+
                    '\nЕДИНЫЕ ИСТОЧНИКИ ФАКТОВ:\n'+evidence_context.render(),
                    stage='полная адресная правка SMM-стратегии',report=event)
        repair_system = (strategy_system + '\nТы исправляешь уже созданный документ, а не создаёшь новый. '
                         'Не сокращай разделы, не меняй бизнес, количество публикаций или другие подтверждённые факты. '
                         'Исправь все нарушения исходного задания и проверь, что прежние нарушения исчезли. '
                         'Неподтверждённые коммерческие условия замени на нейтральные действия по уточнению условий; '
                         'не добавляй новые обещания. Верни полный исправленный документ в Markdown.')
        return _ask(model, repair_system,
                    'ИСХОДНОЕ ЗАДАНИЕ (неизменно):\n' + original +
                    '\nПРИЧИНЫ ОТКЛОНЕНИЯ:\n' + failure[:1500] +
                    '\nОТКЛОНЁННАЯ СТРАТЕГИЯ ДЛЯ АДРЕСНОГО ИСПРАВЛЕНИЯ:\n' + rejected_document+
                    '\nСТРУКТУРИРОВАННЫЕ ПОДТВЕРЖДЁННЫЕ ЗАМЕЧАНИЯ:\n'+json.dumps(structured_findings,ensure_ascii=False)+
                    '\nЕДИНЫЕ ИСТОЧНИКИ ФАКТОВ:\n'+evidence_context.render(),
                    stage='адресное исправление SMM-стратегии',report=event)
    try:
        from document_recovery import DocumentDraft
        candidate=DocumentDraft(drafts,signature,'strategy')
        draft=candidate.load() if context.get('strict_document_recovery') and existing_strategy is None else None
        if draft is None and revision_id and isinstance(revision_seed.get('strategy'),str):
            draft={'content':revision_seed['strategy'],'valid':False,'reason':''}
        if draft is None and task_mode == 'final_review_repair' and existing_strategy is None:
            # In-memory callers need the same exact rejected draft as durable
            # project workspaces; never regenerate it because a file is absent.
            rejected=next((d for d in context.get('rejected_documents', [])
                if isinstance(d,dict) and d.get('kind')=='strategy'
                and isinstance(d.get('content'),str) and d['content'].strip()
                and d.get('already_saved') is not True),None)
            if rejected:
                draft={'content':rejected['content'],'valid':False,
                       'reason':str(rejected.get('reason') or '')}
        # A strategy can pass its strategy-only review and still fail the
        # complete-delivery review (for example, when the inventory finds an
        # unsupported business claim). Carry that final-review verdict back to
        # the exact unapproved strategy checkpoint. Without this hand-off the
        # recovery path sees a valid draft with no rejection reason, reuses it,
        # and repeats the same failure on every final-review cycle.
        if (draft is not None and task_mode == 'final_review_repair' and
                isinstance(context.get('final_review_issues'), list)):
            strategy_issues=[item for item in context['final_review_issues']
                             if isinstance(item,dict) and item.get('document')=='strategy']
            if strategy_issues:
                final_feedback='; '.join(
                    str(item.get('reason') or 'Итоговая проверка отклонила утверждение')+
                    ('; цитата: '+repr(str(item.get('quote') or item.get('claim') or '')[:400])
                     if item.get('quote') or item.get('claim') else '')
                    for item in strategy_issues)[:1800]
                prior_reason=str(draft.get('reason') or '').strip()
                draft=dict(draft)
                draft['valid']=False
                draft['reason']='; '.join(x for x in (prior_reason,final_feedback) if x)[:2200]
                event('strategy_final_review_recovery_started',
                      issue_count=len(strategy_issues),
                      candidate_sha256=hashlib.sha256(
                          str(draft.get('content') or '').encode('utf-8')).hexdigest(),
                      checkpoint_preserved=True)
        final_revision = is_targeted_recovery(context)
        revision_args = {'max_revision_calls':1} if final_revision else {}
        strategy,valid,reason=produce_checked_strategy(
            original,per_week,strategy_attempt,validate_document,
            existing=existing_strategy,report=event,
            attempts=attempt_limit(context,3),
            repair=strategy_repair if final_revision else None if first_pass_acceptance else strategy_repair,
            review=context.get('strategy_reviewer'),draft=draft,
            checkpoint=candidate.save if context.get('strict_document_recovery') else None,
            evidence_context=evidence_context,
            decode=lambda raw:_decode_strategy_proposal(raw,channel_context=channels[0]),
            **revision_args)
    except Exception as exc:
        from text_task_contract import record_model_failure
        failure_file=record_model_failure(drafts,signature,model,'strategy_generation',exc)
        if failure_file:
            event('model_failure_evidence_saved',stage_name='strategy_generation',file=failure_file)
        event('task_failed',step='strategy_generation',error=str(exc)[:400])
        from provider_errors import AuthorExecutionAborted
        return {'action':'compound_documents','instruction':original,'documents':[],
                'error':'Не удалось создать стратегию: '+str(exc),
                **({'failure_code':'author_execution_aborted'} if isinstance(exc,AuthorExecutionAborted) else {})}
    docs.append({'kind':'strategy','title':'SMM-стратегия','content':strategy,'valid':bool(valid),'reason':reason,
                 'already_saved':bool(existing_strategy and any(
                     isinstance(d,dict) and d.get('kind')=='strategy' and d.get('content')==existing_strategy
                     and d.get('already_saved') is True for d in context.get('resume_documents',[])))})
    if not valid:
        ledger.set_status('strategy','blocked',reason=reason)
        event('task_failed',step='strategy_validation',error=reason)
        result={'action':'compound_documents','instruction':original,'documents':docs}
        if strategy_review_unavailable:
            result['failure_code']='independent_review_unavailable'
        from independent_quality_gate import bind_stage_rejection
        return bind_stage_rejection(result,context.get('strategy_review_findings') or [])
    ledger.set_status('strategy','accepted')
    # Plan distinct, addressable publication slots BEFORE asking the language model
    # to write prose. A failed batch cannot poison or erase successful batches.
    planning_error = None
    try:
        planner_args = {'on_week': save_plan, 'existing_slots':cached_slots}
        if first_pass_acceptance:
            planner_args['first_pass_only'] = True
        if per_week != 3:
            planner_args['per_week'] = per_week
        editorial_slots=plan_slots(lambda m,sys,usr: _ask(m,sys,usr,stage='планирование тем',report=event),
                                   model,original,strategy,brief,weeks,**planner_args)
    except Exception as exc:
        planning_error = str(exc)
        event('topic_planning_degraded',step='topic_planning',error=str(exc)[:400],
              action='continue_with_verified_weeks')
        # The planner persists each accepted week; reload its latest checkpoint,
        # not the stale snapshot captured before planning began.
        latest_slots = cached_slots
        if plan_file and plan_file.is_file():
            try:
                latest = json.loads(plan_file.read_text(encoding='utf-8'))
                if isinstance(latest,dict) and latest.get('signature') == signature and isinstance(latest.get('slots'), list):
                    latest_slots = latest['slots']
            except (OSError, ValueError, TypeError):
                event('topic_checkpoint_read_error')
        # Keep completed weeks useful: produce and checkpoint their actual posts
        # rather than returning an empty calendar before publication writing starts.
        editorial_slots = latest_slots
        event('topic_plan_partial',planned=len(editorial_slots),total=scope.total,
              reason=planning_error[:250])
    if not isinstance(editorial_slots,list):
        planning_error = 'Планировщик вернул некорректный тип плана'
        editorial_slots = []
        event('topic_plan_invalid',reason=planning_error)
    if planning_error is None:
        event('topic_plan_completed',planned=len(editorial_slots),total=scope.total)
    # Stage 75: the whole plan, not merely an individual post, receives its own
    # independent cross-slot and business-context review BEFORE production.
    # A preflight failure cannot mutate accepted publications, and a missing
    # model response cannot be falsely reported as semantic approval.
    preflight_blocked={}
    try:
        def ask_plan_reviewer(m,sys,usr,**kw):
            from ollama_resilience import model_call_role
            role='reviewer_backup' if kw.get('backup_reviewer') else 'reviewer'
            with model_call_role(role):
                return _ask(m,sys,usr,stage='проверка всего плана',report=event,
                            json_schema=kw.get('json_schema'))
        governance_args={'expected_ids':required_ids,
            'review_model':context.get('plan_reviewer_models') or context.get('plan_reviewer_model') or model,
            'on_event':event}
        if first_pass_acceptance:
            governance_args['allow_replan']=bool(recovery.get('plan_targets'))
        if recovery.get('plan_targets'):
            governance_args.update(repair_targets=set(recovery['plan_targets']),max_candidates=1)
        governed, preflight_blocked, reviewer_available=govern_plan(
            ask_plan_reviewer,model,original,brief,strategy,editorial_slots,
            set(accepted_posts),**governance_args)
        if governed != editorial_slots:
            # A rejected draft belongs to its old approved topic. After a
            # governed replan it is no longer a valid candidate for repair.
            old_by_id={slot_id(s):s.get('topic') for s in editorial_slots}
            for updated in governed:
                ident=slot_id(updated)
                if old_by_id.get(ident) != updated.get('topic') and ident in repair_drafts:
                    forget_rejection(ident)
                    event('obsolete_repair_draft_cleared',publication=ident)
            if plan_file:
                payload={'signature':signature,'slots':governed,
                         'updated_at':datetime.now(timezone.utc).isoformat()}
                atomic_write_json(plan_file,payload)
            editorial_slots=governed
        event('plan_preflight_result',reviewer_available=reviewer_available,
              blocked=len(preflight_blocked),accepted_checkpoint=len(accepted_posts))
    except Exception as exc:
        event('plan_preflight_error',error=str(exc)[:240])
        reviewer_available=False
        preflight_blocked={ident:'Независимая проверка плана завершилась ошибкой'
                           for ident in required_ids if ident not in accepted_posts}
    # Planner/LLM data are untrusted even when the preflight reviewer crashed.
    # Filter malformed/unexpected IDs before indexing them in production loops;
    # never generate for a slot that was not registered by the application.
    clean_slots=[]
    seen_slots=set()
    invalid_slot_count=0 if isinstance(editorial_slots,list) else 1
    for candidate in (editorial_slots if isinstance(editorial_slots,list) else []):
        if not isinstance(candidate,dict) or type(candidate.get('week')) is not int or not isinstance(candidate.get('day'),str):
            invalid_slot_count+=1
            continue
        ident=slot_id(candidate)
        if ident not in required_ids or ident in seen_slots:
            invalid_slot_count+=1
            continue
        clean_slots.append(candidate)
        seen_slots.add(ident)
    if invalid_slot_count:
        reviewer_available=False
        for ident in required_ids:
            if ident not in accepted_posts:
                preflight_blocked[ident]='План содержит неожиданные или повторные слоты'
        event('plan_schema_rejected',count=invalid_slot_count)
    editorial_slots=clean_slots
    sections=[]
    repair_log=[]
    if planning_error:
        repair_log.append('Планирование незавершено: '+planning_error[:320])
    # Compare accepted copy across all weeks, including resumed checkpoints.
    # A unique topic does not guarantee a unique finished publication.
    def copy_fingerprint(post):
        return re.sub(r'\W+', ' ', str(post.get('caption') or '').casefold()).strip()
    used_copy = set()
    accepted_captions = []
    replan_reasons = {}
    copy_repeat_counts = {}
    def topic_scope_conflict(issues):
        """Narrow trigger: a semantic review must explicitly reject a topic's fit."""
        from task_semantics import ai_content_review_active
        if ai_content_review_active():
            return any(isinstance(issue,dict) and issue.get('field')=='topic'
                       and (issue.get('finding_validation') or {}).get('decision')=='confirmed'
                       and (issue.get('finding_validation') or {}).get('remedy_scope')=='current'
                       for issue in issues)
        for issue in issues:
            message = str(issue).casefold()
            if ('тем' in message or 'рубр' in message) and (
                'не соответствует' in message or 'не относится' in message or
                'противоречит' in message or 'не связана' in message
            ) and any(term in message for term in (
                'бизнес', 'позиционир', 'специализац', 'исходн', 'направлен', 'магазин',
                'ассортимент', 'деятельност', 'задани', 'проект'
            )):
                return True
        return False
    def adapt_model_schema(candidate, ident, slot=None):
        from publication_writer import bind_post_to_slot
        normalized, fields = normalize_model_post(candidate)
        if slot is None:
            slot = next((item for item in editorial_slots if slot_id(item) == ident), None)
        if slot is not None:
            normalized, bound = bind_post_to_slot(normalized, slot)
            if bound:
                event('plan_fields_bound', publication=ident, fields=list(bound))
        if fields:
            event('model_schema_normalized', publication=ident, fields=list(fields))
        return normalized

    unavailable_publications=set()
    def independent_edit(candidate, slot, ident):
        candidate = adapt_model_schema(candidate, ident, slot)
        # Reviewer findings never erase the rejected input. A reviewer may not
        # become the author of content that it approves itself.
        try:
            reviewers=(context.get('publication_reviewer_models') or
                       context.get('plan_reviewer_models') or
                       context.get('plan_reviewer_model') or model)
            reviewed, issues = audit_post_with_independent_model(
                lambda m, sys, usr, **kw: _ask(m, sys, usr,
                       stage=f'неделя {slot["week"]}: независимая редактура {ident}',report=event,
                       json_schema=kw.get('json_schema')),
                model, reviewers, original, known, slot, candidate,
                accepted_captions + ['Ранее утверждённая тема: '+t for t in all_topics],
                report=event,audit_function=audit_post)
            unavailable_publications.discard(ident)
        except Exception as exc:
            unavailable_publications.add(ident)
            event('semantic_review_error', publication=ident,error=str(exc)[:180])
            preserved=getattr(exc,'findings',[])
            return candidate, [*(preserved if isinstance(preserved,list) else []),
                'Не удалось выполнить независимую редактуру: '+str(exc)[:100]]
        if issues:
            if topic_scope_conflict(issues):
                replan_reasons[ident] = issues
            event('semantic_quality_rejected',publication=ident,issues=issues)
            return candidate, issues
        reviewed = adapt_model_schema(reviewed, ident, slot)
        reviewed, metadata_added = add_production_materials(reviewed,slot)
        problems = validate_single(reviewed,slot,all_topics)
        problems += critical_issues({**reviewed,'format':slot.get('format')})
        problems += editorial_risks(reviewed,original,evidence_context)
        problems += content_consistency_issues(reviewed,slot,original)
        # This is a verified, recoverable publication checkpoint, not approval
        # of the complete user delivery. Full business-claim discovery and
        # source adjudication run once at the production boundary after both
        # strategy and calendar are assembled. Failed final review therefore
        # leaves these bytes available for targeted recovery without declaring
        # the task complete.
        if copy_fingerprint(reviewed) in used_copy or copy_too_similar(reviewed.get('caption'),accepted_captions):
            problems.append('Проверенный текст похож на ранее принятый')
        if problems:
            event('semantic_revision_rejected',publication=ident,issues=problems)
            return reviewed, problems
        reviewed['_editorial_review'] = REVIEW_VERSION
        reviewed['_editorial_policy'] = POLICY_VERSION
        event('semantic_review_passed',publication=ident,changed=reviewed.get('caption') != candidate.get('caption'))
        return reviewed, []
    platform=known.get('platform')
    if not isinstance(platform,str) or not platform.strip():
        return {'action':'compound_clarify','instruction':original,
                'answer':'Не удалось определить площадку без догадки. Укажите канал для публикаций.'}
    # Existing task-level checkpoint and diagnostics remain active for all weeks.
    all_topics=[]
    cached_concerns=[]
    author_generation_stop=False
    for week in range(1,weeks+1):
        if author_generation_stop:
            event('remaining_slots_left_pending_after_author_budget_stop',week=week)
            break
        slots=[s for s in editorial_slots if s['week']==week]
        if not slots:
            sections.append(f'## Неделя {week} — ещё не спланирована')
            event('week_skipped_unplanned',week=week,planned=len(editorial_slots),total=scope.total)
            continue
        week_posts=[]
        pending=[]
        for slot in slots:
            ident=f"{slot['week']}/{slot['day']}"
            cached=accepted_posts.get(ident)
            if isinstance(cached,dict):
                # An already accepted checkpoint is immutable on resume, even if
                # an upgraded editor now disagrees. Report the discrepancy and
                # require an explicit edit operation; never silently unset or
                # overwrite the user's approved result.
                clean = (cached.get('_editorial_review') == REVIEW_VERSION
                         and cached.get('_editorial_policy') == POLICY_VERSION
                         and copy_fingerprint(cached) not in used_copy
                         and not copy_too_similar(cached.get('caption'),accepted_captions)
                         and not validate_single(cached,slot,all_topics)
                         and not critical_issues({**cached,'format':slot.get('format')})
                         and not editorial_risks(cached,original,evidence_context)
                         and not content_consistency_issues(cached,slot,original))
                week_posts.append(cached)
                all_topics.append(str(cached.get('topic') or ''))
                used_copy.add(copy_fingerprint(cached))
                accepted_captions.append(str(cached.get('caption') or ''))
                if not clean:
                    message = f'{ident}: ранее принятая публикация требует явной повторной проверки/редактирования'
                    cached_concerns.append(message)
                    event('cached_preserved_with_review_warning',week=week,publication=ident)
                continue
            else:
                # Malformed checkpoint values are not accepted and are never
                # trusted as a post object.
                if cached is not None:
                    cached_concerns.append(f'{ident}: повреждённая запись контрольной точки')
                if ident in preflight_blocked:
                    reason=preflight_blocked[ident]
                    repair_log.append(f'{ident}: неверный план до генерации — {reason}')
                    ledger.set_status(ident,'blocked',reason=reason)
                    event('publication_blocked_by_plan',week=week,publication=ident,reason=reason)
                else:
                    pending.append(slot)
        if pending:
            # Stage 73: old rejected drafts can be structurally valid after a
            # lossless schema adaptation. Re-check them with ALL quality gates,
            # including a fresh independent semantic audit, before generation.
            for slot in list(pending):
                ident = f"{slot['week']}/{slot['day']}"
                saved = repair_drafts.get(ident)
                prior = saved.get('post') if isinstance(saved, dict) else None
                if not isinstance(prior,dict):
                    continue
                normalized, changed = normalize_model_post(prior)
                if not changed and saved.get('review_state')!='unavailable':
                    continue
                event('schema_checkpoint_recovery_started', week=week,
                      publication=ident, fields=list(changed))
                normalized, _ = add_production_materials(normalized, slot)
                problems = validate_single(normalized,slot,all_topics)
                problems += critical_issues({**normalized,'format':slot.get('format')})
                problems += editorial_risks(normalized,original,evidence_context)
                problems += content_consistency_issues(normalized,slot,original)
                if (copy_fingerprint(normalized) in used_copy or
                        copy_too_similar(normalized.get('caption'),accepted_captions)):
                    problems.append('текст повторяет ранее принятую публикацию')
                if not problems:
                    reviewed, problems = independent_edit(normalized,slot,ident)
                if problems:
                    remember_rejection(ident,normalized,problems)
                    event('schema_checkpoint_recovery_rejected',week=week,
                          publication=ident,issues=problems[:5])
                    continue
                accepted_posts[ident] = reviewed
                forget_rejection(ident)
                week_posts.append(reviewed)
                all_topics.append(reviewed['topic'])
                used_copy.add(copy_fingerprint(reviewed))
                accepted_captions.append(reviewed.get('caption',''))
                pending.remove(slot)
                checkpoint()
                event('schema_checkpoint_recovered', week=week,publication=ident,
                      completed=len(accepted_posts),total=scope.total)
        if pending:
            if not final_review_repair_slots:
                # One model request per week, with one bounded repair request.
                failures=[]
                for attempt in range(attempt_limit(context,2)):
                    generation_slots=[s for s in pending if slot_id(s) not in unavailable_publications
                        and not (revision_id and slot_id(s) in repair_drafts)]
                    if not generation_slots:
                        break
                    try:
                        candidates=write_week(lambda m, sys, usr, **kw: _ask(m,sys,usr,stage=f'неделя {week}: генерация',report=event,json_schema=kw.get('json_schema')),model,original,known,strategy,generation_slots,all_topics,
                                              feedback='; '.join(failures[-6:]))
                        by_id = {}
                        # Match by unique *approved plan topic* first, not by model
                        # generated ID. Qwen may duplicate IDs in a batch while
                        # leaving correctly assigned publication topics intact.
                        # Never infer ownership merely from array position.
                        for slot in generation_slots:
                            ident = f"{slot['week']}/{slot['day']}"
                            topic = str(slot.get('topic') or '').strip().casefold()
                            matches = [p for p in candidates if isinstance(p,dict)
                                       and str(p.get('topic') or '').strip().casefold() == topic]
                            if len(matches) == 1:
                                item = matches[0]
                                if str(item.get('id')) != ident:
                                    event('id_reconciled',week=week,publication=ident)
                                by_id[ident] = {**item, 'id':ident}
                            else:
                                item = next((p for p in candidates if isinstance(p,dict)
                                             and str(p.get('id')) == ident), None)
                                if item is not None:
                                    # Only validate_single can decide if the topic
                                    # really belongs to this destination.
                                    by_id[ident] = item
                        for slot in list(generation_slots):
                            ident=f"{slot['week']}/{slot['day']}"
                            candidate=by_id.get(ident)
                            if candidate is None:
                                failures.append(ident+': отсутствует в ответе')
                                continue
                            candidate = adapt_model_schema(candidate, ident)
                            candidate, metadata_added = add_production_materials(candidate,slot)
                            remember_rejection(ident, candidate, [], preserve_existing=True)
                            if metadata_added:
                                event('production_metadata_completed',week=week,publication=ident)
                            errors=validate_single(candidate,slot,all_topics)
                            errors += editorial_risks(candidate,original,evidence_context)
                            errors += content_consistency_issues(candidate,slot,original)
                            if errors:
                                remember_rejection(ident,candidate,errors,preserve_existing=True)
                                failures.append(ident+': '+', '.join(errors))
                                continue
                            if copy_fingerprint(candidate) in used_copy or copy_too_similar(candidate.get('caption'), accepted_captions):
                                remember_rejection(ident,candidate,['Текст повторяет уже принятую публикацию'],preserve_existing=True)
                                failures.append(ident+': текст повторяет или чрезмерно похож на ранее принятый')
                                event('duplicate_copy_rejected',week=week,publication=ident)
                                continue
                            critical=critical_issues({**candidate, "format":slot.get("format")})
                            if critical:
                                # Never checkpoint an unresolved critical-risk post.
                                if first_pass_acceptance:
                                    remember_rejection(ident,candidate,critical,preserve_existing=True)
                                    failures.append(ident+': '+', '.join(critical[:5]))
                                    event('acceptance_first_pass_critical_rejection',
                                          week=week,publication=ident,issue_count=len(critical),
                                          repair_call_skipped=True)
                                    continue
                                try:
                                    from publication_writer import _parse_json
                                    system=('Ты профессиональный SMM-редактор для направления из задания. Верни ТОЛЬКО JSON '
                                            '{"posts":[...]}, ровно один объект с id,topic,visual,caption,cta,kpi. '
                                            'Исправь все риски и клише; никаких выдуманных графиков, приложений, '
                                            'сайтов, услуг, ссылок или неподтверждённых гарантий. '
                                            'Укажи практические шаги и конкретные кадры. '
                                            'Если факт не подтверждён, не упоминай его. '
                                            'Сохрани тему, формат и прежний id.')
                                    payload={'post':candidate,'issues':critical,'known_facts':known,
                                             'format':slot.get('format')}
                                    revised=_parse_json(_ask(model,system,json.dumps(payload,ensure_ascii=False),
                                                stage=f'неделя {week}: редактура {ident}',report=event))
                                    if isinstance(revised,list) and len(revised)==1 and revised[0].get('id')==ident:
                                        fixed,_=add_production_materials(adapt_model_schema(revised[0],ident),slot)
                                        remaining=validate_single(fixed,slot,all_topics)+critical_issues({**fixed, "format":slot.get("format")})+editorial_risks(fixed,original,evidence_context)
                                        if not remaining:
                                            candidate=fixed
                                            if copy_fingerprint(candidate) in used_copy or copy_too_similar(candidate.get('caption'),accepted_captions):
                                                failures.append(ident+': редактура повторяет ранее принятый текст')
                                                continue
                                            event('quality_repaired',week=week,publication=ident)
                                        else:
                                            remember_rejection(ident,candidate,remaining,preserve_existing=True)
                                            failures.append(ident+': редактура не прошла: '+', '.join(remaining))
                                            event('quality_unresolved',week=week,publication=ident,issues=remaining)
                                            continue
                                    else:
                                        failures.append(ident+': некорректный ответ редактора')
                                        event('quality_unresolved',week=week,publication=ident,issues=critical)
                                        continue
                                except Exception as exc:
                                    failures.append(ident+': ошибка редактуры: '+str(exc)[:100])
                                    event('quality_repair_error',week=week,publication=ident,error=str(exc)[:120])
                                    continue
                            candidate, review_errors = independent_edit(candidate,slot,ident)
                            if review_errors:
                                remember_rejection(ident,candidate,review_errors,preserve_existing=True)
                                failures.append(ident+': '+', '.join(format_repair_issues(review_errors)))
                                continue
                            accepted_posts[ident]=candidate
                            forget_rejection(ident)
                            week_posts.append(candidate)
                            all_topics.append(candidate['topic'])
                            used_copy.add(copy_fingerprint(candidate))
                            accepted_captions.append(candidate.get('caption',''))
                            pending.remove(slot)
                            checkpoint()
                        if not pending: break
                    except Exception as exc:
                        rejected_raw = getattr(exc, 'response_text', None)
                        if rejected_raw is None and isinstance(exc, json.JSONDecodeError):
                            rejected_raw = exc.doc
                        if isinstance(rejected_raw, str) and drafts:
                            rejected_file = drafts / (f'model_response_rejected_{signature[:16]}_{week}_{attempt+1}.json')
                            atomic_write_json(rejected_file, {
                                'signature': signature, 'model': model, 'week': week,
                                'stage': getattr(exc, 'stage', 'publication_generation'),
                                'error_type': type(exc).__name__, 'response': rejected_raw,
                            })
                            event('model_response_contract_rejected', week=week,
                                  artifact=rejected_file.name, response_chars=len(rejected_raw))
                        failures.append(type(exc).__name__+': '+str(exc)[:160])
                        if _terminal_acceptance_budget_error(exc):
                            author_generation_stop=True
                            event('author_case_stop_after_budget',week=week,
                                  attempt=attempt+1)
                            break
                    event('batch_attempt',week=week,attempt=attempt+1,completed=len(accepted_posts),
                          total=scope.total,errors=failures[-6:])
                # A malformed weekly batch must not prevent independent posts from
                # being generated and checkpointed. Never accept a fallback without
                # passing the same structural and editorial checks as batch posts.
            if initial_generation_only(context) and pending:
                # Acceptance records the first generated batch as-is. Individual
                # rewrites belong to production recovery and must not turn a fast
                # randomized benchmark into a multi-minute repair loop.
                author_generation_stop=True
                event('acceptance_first_pass_incomplete',week=week,
                      pending_slots=[f"{x['week']}/{x['day']}" for x in pending],
                      repair_calls_skipped=True)
            for slot in list(pending):
                if author_generation_stop:
                    break
                ident = f"{slot['week']}/{slot['day']}"
                if ident in unavailable_publications:
                    event('author_repair_skipped_unavailable_review',publication=ident)
                    continue
                local_issues = []
                # Model/HTTP failures and rejected drafts can be repaired in
                # place, with bounded retries and one fresh regeneration when
                # repeated revisions do not converge.
                # New Stage 72 recovery schedule: try a bounded field patch when
                # feedback is localized, then full rewrite and fresh fallback.
                # Preserve the Stage 71 editorial gate with no automatic approvals.
                for single_attempt in range(attempt_limit(context,7)):
                    if ident in unavailable_publications:
                        break
                    saved = repair_drafts.get(ident, {})
                    failed_post = saved.get('post') if isinstance(saved,dict) else None
                    saved_issues = saved.get('issues',[]) if isinstance(saved,dict) else []
                    rejected_captions = saved.get('rejected_captions',[]) if isinstance(saved,dict) else []
                    issues_to_fix = local_issues or saved_issues
                    if rejected_captions:
                        issues_to_fix = list(issues_to_fix) + [
                            'Не повторяй отклонённые формулировки этого слота: ' +
                            ' / '.join(str(x)[:320] for x in rejected_captions[-3:])]
                    try:
                        if (isinstance(failed_post,dict) and
                                single_attempt in (0,3) and
                                repair_field_targets(issues_to_fix)):
                            event('field_patch_started',week=week,publication=ident,
                                  attempt=single_attempt+1,issues=issues_to_fix[-5:])
                            try:
                                candidate=repair_fields(
                                    lambda m,sys,usr,**kw: _ask(m,sys,usr,
                                        stage=f'неделя {week}: правка полей {ident}',report=event,json_schema=kw.get('json_schema')),
                                    model,original,known,strategy,slot,failed_post,
                                    issues_to_fix,all_topics)
                            except (ValueError,TypeError,KeyError) as exc:
                                event('field_patch_fallback',week=week,publication=ident,
                                      error=str(exc)[:150])
                                candidate=repair_single(
                                    lambda m,sys,usr,**kw: _ask(m,sys,usr,
                                        stage=f'неделя {week}: адресная правка {ident}',report=event,json_schema=kw.get('json_schema')),
                                    model,original,known,strategy,slot,failed_post,
                                    issues_to_fix,all_topics)
                        elif (isinstance(failed_post,dict) and
                              (single_attempt in (1,4,6) or
                               (ident in final_review_repair_slots and single_attempt==0))):
                            event('targeted_repair_started',week=week,publication=ident,
                                  attempt=single_attempt+1,issues=issues_to_fix[-5:])
                            candidate=repair_single(
                                lambda m,sys,usr,**kw: _ask(m,sys,usr,
                                    stage=f'неделя {week}: адресная правка {ident}',report=event,json_schema=kw.get('json_schema')),
                                model,original,known,strategy,slot,failed_post,
                                issues_to_fix,all_topics)
                        else:
                            event('fresh_recovery_started',week=week,publication=ident,
                                  attempt=single_attempt+1)
                            candidate = write_single(
                                lambda m,sys,usr,**kw: _ask(m,sys,usr,
                                     stage=f'неделя {week}: отдельная публикация {ident}',report=event,json_schema=kw.get('json_schema')),
                                model, original, known, strategy, slot, all_topics,
                                feedback='; '.join(format_repair_issues(issues_to_fix)[-5:]))
                        candidate = adapt_model_schema(candidate, ident)
                        candidate, metadata_added = add_production_materials(candidate, slot)
                        if metadata_added:
                            event('production_metadata_completed',week=week,publication=ident)
                        errors = validate_single(candidate, slot, all_topics)
                        errors += critical_issues({**candidate, 'format': slot.get('format')})
                        errors += editorial_risks(candidate,original,evidence_context)
                        errors += content_consistency_issues(candidate,slot,original)
                        # Retention is a review hint, not semantic rejection.
                        # A localized edit deliberately preserves other fields.
                        if ident in final_review_repair_slots:
                            retained = repeated_rejected_claims(
                                candidate, final_review_issues_by_slot.get(
                                    ident, context.get('final_review_issues', [])))
                            if retained:
                                event('repair_retention_requires_review',publication=ident,hints=retained)
                        repeats_copy = (copy_fingerprint(candidate) in used_copy or
                                copy_too_similar(candidate.get('caption'),accepted_captions))
                        if repeats_copy:
                            copy_repeat_counts[ident] = copy_repeat_counts.get(ident, 0) + 1
                            errors.append('текст повторяет другую уже принятую публикацию')
                        if errors:
                            local_issues = deduplicate_findings(errors)
                            remember_rejection(ident,candidate,local_issues)
                            if repeats_copy and copy_repeat_counts[ident] >= 2:
                                replan_reasons[ident] = local_issues
                                event('content_diversity_replan_requested',week=week,
                                      publication=ident,reason='повторная формулировка после проверки')
                            event('single_quality_rejected',week=week,publication=ident,
                                  attempt=single_attempt+1,issues=local_issues)
                            if repeats_copy and copy_repeat_counts[ident] >= 2:
                                break
                            continue
                        reviewed, review_errors = independent_edit(candidate,slot,ident)
                        if review_errors:
                            local_issues = deduplicate_findings(review_errors)
                            remember_rejection(ident,candidate,local_issues)
                            event('single_semantic_rejected',week=week,publication=ident,
                                  attempt=single_attempt+1,issues=local_issues)
                            continue
                        accepted_posts[ident] = reviewed
                        forget_rejection(ident)
                        week_posts.append(reviewed)
                        all_topics.append(reviewed['topic'])
                        used_copy.add(copy_fingerprint(reviewed))
                        accepted_captions.append(reviewed.get('caption',''))
                        pending.remove(slot)
                        checkpoint()
                        event('single_accepted',week=week,publication=ident,
                              completed=len(accepted_posts),total=scope.total)
                        break
                    except Exception as exc:
                        # No replacement artifact exists after a generation
                        # failure. Keep the findings for the unchanged draft;
                        # a transport/protocol exception is not new content QA.
                        local_issues = deduplicate_findings(saved_issues + local_issues)
                        event('single_generation_error',week=week,publication=ident,
                              attempt=single_attempt+1,error=str(exc)[:180])
                        if _terminal_acceptance_budget_error(exc):
                            author_generation_stop=True
                            event('author_case_stop_after_budget',week=week,publication=ident,
                                  attempt=single_attempt+1)
                            break
                # Replan only an unaccepted slot, and only when semantic review
                # found a scope conflict or retries demonstrate that the topic
                # cannot produce distinct publishable copy. The proposed topic
                # and complete plan are independently reviewed before commit.
                if (first_pass_acceptance and slot in pending and ident in replan_reasons):
                    event('topic_replan_skipped_first_pass',week=week,publication=ident)
                if (not first_pass_acceptance and ident not in unavailable_publications
                        and slot in pending and ident in replan_reasons and
                        str(slot.get('topic') or '').casefold() not in original.casefold()):
                    event('topic_replan_recovery_started',week=week,publication=ident,
                          issues=replan_reasons[ident][-3:])
                    # Try multiple MODEL-PROPOSED directions, not the same
                    # rejected topic twice. No slot is changed until a complete
                    # publication passes all deterministic + independent QA.
                    rejected_topics=[]
                    for plan_attempt in range(2):
                        if slot not in pending or ident in unavailable_publications:
                            break
                        try:
                            alternatives=propose_topic_replacement(
                                lambda m,sys,usr: _ask(m,sys,usr,
                                    stage=f'неделя {week}: перепланирование {ident}',report=event),
                                model,original,strategy,known,slot,
                                all_topics + [s['topic'] for s in editorial_slots if s is not slot],
                                replan_reasons[ident], rejected_topics=rejected_topics,
                                return_all=True)
                            # Accept legacy one-proposal responses from older
                            # model adapters and test doubles.
                            if isinstance(alternatives,dict):
                                alternatives=[alternatives]
                            if not isinstance(alternatives,list) or not alternatives:
                                raise ValueError('Редактор плана не предложил допустимых тем')
                            event('topic_scope_candidates',week=week,publication=ident,
                                  attempt=plan_attempt+1,count=len(alternatives))
                        except Exception as exc:
                            event('topic_scope_recovery_failed',week=week,publication=ident,
                                  attempt=plan_attempt+1,error=str(exc)[:260])
                            continue
                        for alternative in alternatives:
                            if slot not in pending or ident in unavailable_publications:
                                break
                            proposal_topic=str(alternative.get('topic') or '')
                            try:
                                if any(alternative.get(k)!=slot.get(k)
                                       for k in ('week','day','format')):
                                    raise ValueError('Новая тема попыталась сменить слот или формат')
                                if (not proposal_topic or
                                        topic_repeats(proposal_topic,rejected_topics) or
                                        topic_repeats(proposal_topic,
                                            all_topics + [s['topic'] for s in editorial_slots if s is not slot])):
                                    raise ValueError('Тема повторяет принятый или отклонённый план')
                                proposed=[alternative if item is slot else item for item in editorial_slots]
                                def ask_replan_reviewer(m,sys,usr,**kw):
                                    from ollama_resilience import model_call_role
                                    role='reviewer_backup' if kw.get('backup_reviewer') else 'reviewer'
                                    with model_call_role(role):
                                        return _ask(m,sys,usr,
                                            stage='повторная проверка всего плана',report=event,
                                            json_schema=kw.get('json_schema'))
                                governance_args={'expected_ids':required_ids,
                                    'review_model':context.get('plan_reviewer_models') or context.get('plan_reviewer_model') or model,
                                    'on_event':event}
                                if first_pass_acceptance:
                                    governance_args['allow_replan']=False
                                checked, blocked, available=govern_plan(
                                    ask_replan_reviewer,model,original,brief,strategy,
                                    proposed,set(accepted_posts),**governance_args)
                                if not available:
                                    unavailable_publications.add(ident)
                                    event('plan_reviewer_unavailable_during_recovery',publication=ident)
                                if not available or blocked or checked != proposed:
                                    raise ValueError('Изменённый полный план не получил независимого одобрения')
                                fresh=write_single(
                                    lambda m,sys,usr,**kw: _ask(m,sys,usr,
                                       stage=f'неделя {week}: новая тема {ident}',report=event,json_schema=kw.get('json_schema')),
                                    model,original,known,strategy,alternative,all_topics)
                                fresh=adapt_model_schema(fresh,ident,alternative)
                                fresh,_=add_production_materials(fresh,alternative)
                                errors=validate_single(fresh,alternative,all_topics)
                                errors+=critical_issues({**fresh,'format':alternative.get('format')})
                                errors+=editorial_risks(fresh,original,evidence_context)
                                errors+=content_consistency_issues(fresh,alternative,original)
                                if (copy_fingerprint(fresh) in used_copy or
                                        copy_too_similar(fresh.get('caption'),accepted_captions)):
                                    errors.append('текст повторяет ранее принятый')
                                if errors:
                                    raise ValueError('Новое содержание не прошло проверку: '+', '.join(errors[:5]))
                                reviewed,errors=independent_edit(fresh,alternative,ident)
                                if errors:
                                    raise ValueError('Новая тема не прошла редактуру: '+', '.join(format_repair_issues(errors[:4])))
                                old_topic=slot['topic']
                                # A plan mutation is permitted only *after*
                                # content QA and only for this unaccepted slot.
                                if plan_file:
                                    revised_slots=[(alternative if item is slot else item)
                                                   for item in editorial_slots]
                                    payload={'signature':signature,'slots':revised_slots,
                                             'updated_at':datetime.now(timezone.utc).isoformat()}
                                    atomic_write_json(plan_file,payload)
                                slot.update({key:alternative[key] for key in ('topic','brief','goal','audience')})
                                accepted_posts[ident]=reviewed
                                forget_rejection(ident)
                                week_posts.append(reviewed)
                                all_topics.append(reviewed['topic'])
                                used_copy.add(copy_fingerprint(reviewed))
                                accepted_captions.append(reviewed.get('caption',''))
                                pending.remove(slot)
                                checkpoint()
                                event('topic_scope_recovered',week=week,publication=ident,
                                      replaced_topic=old_topic,new_topic=slot['topic'],
                                      completed=len(accepted_posts),total=scope.total)
                            except Exception as exc:
                                if proposal_topic:
                                    rejected_topics.append(proposal_topic)
                                event('topic_scope_candidate_rejected',week=week,publication=ident,
                                      attempt=plan_attempt+1,topic=proposal_topic[:150],
                                      error=str(exc)[:290])
                        if slot in pending:
                            event('topic_scope_candidates_exhausted',week=week,publication=ident,
                                  attempt=plan_attempt+1,tried=len(rejected_topics))
                if slot in pending:
                    repair_log.append(ident + ': ' +
                        '; '.join(format_repair_issues((local_issues or repair_drafts.get(ident,{}).get('issues', []))[-6:])))
        checkpoint()
        event('week_done',week=week,completed=len(accepted_posts),total=scope.total,
              failed=len(pending))
        if len(week_posts)==len(slots):
            sections.append(f'## Неделя {week}\n\n'+week_to_markdown(week_posts,slots))
        else:
            sections.append(f'## Неделя {week} — не завершена ({len(week_posts)}/{len(slots)} публикаций проверено)')
            for post in week_posts:
                one_slot=next(s for s in slots if f"{s['week']}/{s['day']}"==post['id'])
                sections.append(week_to_markdown([post],[one_slot]))
    from russian_quantity import publication_count, week_count
    calendar=('# Контент-план\n\nПериод: '+week_count(weeks)+'. Частота: '+
              publication_count(per_week)+' в неделю; всего '+publication_count(scope.total)+'.\n\n'+
              '\n\n'.join(sections))
    issues=list(editorial_audit(calendar,weeks,per_week))
    if not reviewer_available:
        issues.append('Структура или независимый аудит полного плана не подтверждены')
    if preflight_blocked:
        issues.append(f'Не разрешено производство {len(preflight_blocked)} слотов плана')
    if planning_error:
        issues.append('Планирование: '+planning_error[:400])
    if len(editorial_slots) != scope.total:
        issues.append(f'Спланировано {len(editorial_slots)} из {scope.total} публикаций')
    if len(accepted_posts) != scope.total:
        issues.append(f'Проверено и сохранено в контрольных точках {len(accepted_posts)} из {scope.total} публикаций')
    if repair_log:
        issues.append(f'Есть {len(repair_log)} нерешённых проблем планирования или публикаций; необходима адресная доработка')
    issues.extend(cached_concerns)
    # Revision is targeted to incomplete batches, never silently replace all months
    # with a short, generic response.
    if issues:
        reason='; '.join(issues)
        ledger.set_status('calendar','blocked',reason='; '.join(issues)[:400])
        event('task_failed',step='calendar_validation',issues=issues,repair_log=repair_log[:50],completed=len(accepted_posts),total=scope.total)
        docs.append({'kind':'calendar','title':'Контент-план','content':calendar,
                     'valid':False,'reason':reason,'repair_log':repair_log})
    else:
        ledger.set_status('calendar','accepted')
        # Document generation is only ready for the final independent review;
        # it is not completion of the user's end-to-end task.
        event('documents_ready_for_final_review',completed=len(accepted_posts),total=scope.total)
        docs.append({'kind':'calendar','title':'Контент-план','content':calendar,
                     'valid':True,'reason':'','repair_log':repair_log})
    if drafts:
        DocumentDraft(drafts, signature, 'calendar').save(calendar, not bool(issues),
            '; '.join(issues)[:1800] if issues else 'pre-final-review verified candidate')
    report = delivery_report(original, scope, docs, accepted_posts)
    # A central host-owned outcome distinguishes missing content from reviewer
    # unavailability and never declares delivery complete before file receipts.
    from workflow_authority import ExecutionContract, assess_production
    assessment=assess_production(required_ids,accepted_posts,
        plan_reviewed=bool(reviewer_available),
        editor_available=not bool(strategy_review_unavailable or unavailable_publications),
        pending_findings=issues,
        production_error=planning_error)
    report['workflow_assessment']=assessment.record()
    from workflow_authority import next_recovery_action
    report['workflow_next_action']=next_recovery_action(assessment)
    if durable_task_id is not None:
        report['execution_contract']=ExecutionContract.create(
            durable_task_id, original, weeks, per_week).proof()
    # These identifiers bind a persisted acceptance report to this exact
    # assignment, not merely to another task with matching publication count.
    report['task_signature']=signature
    report['request_sha256']=hashlib.sha256(original.encode('utf-8')).hexdigest()
    if durable_task_id is not None:
        report['task_id']=durable_task_id
    ledger.reconcile(accepted_posts.keys(),strategy=bool(docs and docs[0].get('valid')),
                     calendar=not issues)
    report['task_orchestration']=ledger.summary()
    report['plan_preflight']={'reviewer_available':reviewer_available if 'reviewer_available' in locals() else False,
                              'unresolved_slots':sorted(preflight_blocked)}
    from workflow_recovery import capture_failures
    failures=capture_failures(signature,required_ids,editorial_slots,preflight_blocked,
        reviewer_available,{k:v for k,v in repair_drafts.items() if k not in unavailable_publications},
        set(accepted_posts))
    if drafts:
        try:
            from delivery_report_store import store_delivery_report
            store_delivery_report(context['project_workspace'], signature, report, archive=False, stage='draft')
        except OSError:
            event('delivery_report_write_error')
    event('content_delivery_audited', report=report)
    result={'action':'compound_documents','instruction':original,'documents':docs,
            'content_delivery':report,'task_signature':signature,'workflow_failures':failures,
            'recovery_context':{'task_signature':signature,
                'task_contract_signature':context.get('text_task_signature'),
                'analysis':brief,'slots':editorial_slots,'rejected_posts':repair_drafts}}
    if not reviewer_available or unavailable_publications:
        result['failure_code']='independent_review_unavailable'
    return result
