"""Compile a validated semantic decision into an executor context.

Only resource ownership, source provenance and technical tool bounds are checked
here. User language is neither tokenized nor classified.
"""
from __future__ import annotations
import copy
import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager
from dialogue_runtime import DialoguePlan, digest


def validate_live_plan(db,project_id,plan):
    if not isinstance(plan,DialoguePlan) or plan.project_id!=project_id:
        raise ValueError('Реплика относится к другому проекту')
    if plan.action['operation']=='local_media':
        from project_media_sources import resolve_media_ids
        resolve_media_ids(db,project_id,plan.snapshot.get('media',{}),plan.action['media']['source_ids'])
    project=db.execute('SELECT name,category,description FROM projects WHERE id=?',(project_id,)).fetchone()
    if not project or tuple(project)!=tuple(plan.snapshot['project'][key] for key in ('name','category','description')):
        raise ValueError('Контекст проекта изменился во время анализа')
    for ident in plan.action['target_ids']:
        row=db.execute('SELECT kind,title,content,updated_at FROM manager_documents WHERE id=? AND project_id=?',(ident,project_id)).fetchone()
        old=plan.snapshot['documents'][ident]
        if not row or (row[0],row[1],digest(row[2]),row[3])!=(old['kind'],old['title'],old['sha256'],old['updated_at']):
            raise ValueError('Документ изменился во время анализа; требуется новый разбор актуального состояния')
    # Every consulted record must still be the same, even when source_id is current.
    ids=set(plan.action['message_ids'])
    if plan.action['source_id'].startswith('message:'):ids.add(plan.action['source_id'])
    if plan.snapshot.get('current_message_id'):ids.add(plan.snapshot['current_message_id'])
    for ident in ids:
        old=plan.snapshot['messages'][ident]
        current=db.execute('SELECT role,content,created_at FROM messages WHERE project_id=? AND id=?',
                           (project_id,ident.partition(':')[2])).fetchone()
        if not current or tuple(current)!=(old['role'],old['content'],old['created_at']):
            raise ValueError('Исходное сообщение изменилось после анализа диалога')
    pending=db.execute('SELECT request,question,revision_id FROM ai_pending_tasks WHERE project_id=?',(project_id,)).fetchone()
    old=plan.snapshot['sources'].get('pending')
    if (tuple(pending) if pending else None)!=((old['request'],old['question'],old['revision_id']) if old else None):
        raise ValueError('Незавершённое задание изменилось после анализа диалога')
    from task_registry import task_context_json
    active=db.execute("SELECT task_id,legacy_origin FROM ai_task_registry WHERE project_id=? AND status='active'",
                      (project_id,)).fetchone()
    if (active[0] if active else None)!=(old.get('task_id') if old else None):
        raise ValueError('Идентификатор незавершённого задания изменился')
    if old and (bool(active[1]) if active else False)!=bool(old.get('legacy_origin')):
        raise ValueError('Происхождение незавершённого задания изменилось')
    if old and 'execution_context_sha256' in old:
        body=task_context_json(db,project_id,old.get('task_id'))
        if body is None and old.get('legacy_origin') and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_semantic_tasks'").fetchone():
            record=db.execute('SELECT context_json FROM ai_semantic_tasks WHERE project_id=? AND request=?',
                              (project_id,old['request'])).fetchone()
            body=record[0] if record else None
        if (digest(body) if body is not None else None)!=old['execution_context_sha256']:
            raise ValueError('Контракт незавершённого задания изменился')
    from task_registry import paused_tasks
    live_paused={('paused:'+r['task_id']):r for r in paused_tasks(db,project_id)}
    snapshot_paused={k:v for k,v in plan.snapshot['sources'].items() if k.startswith('paused:')}
    if set(live_paused)!=set(snapshot_paused):
        raise ValueError('Список незавершённых заданий изменился после анализа')
    for ident,item in live_paused.items():
        previous=snapshot_paused[ident]
        if (item['request'],item['question'],item['revision_id'],bool(item['legacy_origin'])) != (previous['request'],previous['question'],previous['revision_id'],bool(previous.get('legacy_origin'))):
            raise ValueError('Состояние приостановленного задания изменилось')
        if 'execution_context_sha256' in previous:
            body=task_context_json(db,project_id,item['task_id'])
            if (digest(body) if body is not None else None)!=previous['execution_context_sha256']:
                raise ValueError('Контракт приостановленного задания изменился')
    from question_registry import paused_questions
    live_questions={'question:'+q['question_id']:q for q in paused_questions(db,project_id)}
    snapshot_questions={k:v for k,v in plan.snapshot['sources'].items() if k.startswith('question:')}
    if set(live_questions)!=set(snapshot_questions):
        raise ValueError('Список уточняющих вопросов изменился после анализа')
    for ident,item in live_questions.items():
        old_question=snapshot_questions[ident]
        if (item['request'],item['question'],digest(item['plan_json']) if item['plan_json'] else None)!=(
                old_question['request'],old_question['question'],old_question['question_context_sha256']):
            raise ValueError('Приостановленный вопрос изменился')
    question=db.execute('SELECT request,question FROM ai_dialogue_questions WHERE project_id=?',(project_id,)).fetchone()
    old=plan.snapshot['sources'].get('clarification')
    if (tuple(question) if question else None)!=((old['request'],old['question']) if old else None):
        raise ValueError('Вопрос диалога изменился после анализа')
    if old and 'question_context_sha256' in old:
        current=None
        if 'plan_json' in {r[1] for r in db.execute('PRAGMA table_info(ai_dialogue_questions)')}:
            record=db.execute('SELECT plan_json FROM ai_dialogue_questions WHERE project_id=?',(project_id,)).fetchone()
            if record and record[0]:current=digest(record[0])
        if current!=old['question_context_sha256']:raise ValueError('Связь вопроса с заданием изменилась')


@contextmanager
def _write_transaction(db):
    """Lock before the final state check; nested callers keep their transaction."""
    nested=db.in_transaction;name='state_'+uuid4().hex
    if nested:db.execute('SAVEPOINT '+name)
    else:db.execute('BEGIN IMMEDIATE')
    try:
        yield
        if nested:db.execute('RELEASE SAVEPOINT '+name)
        else:db.commit()
    except BaseException:
        if nested:
            db.execute('ROLLBACK TO SAVEPOINT '+name);db.execute('RELEASE SAVEPOINT '+name)
        else:db.rollback()
        raise



class ProjectConnection(sqlite3.Connection):
    """Let existing storage helpers commit only at the outer result boundary."""
    _result_depth = 0

    def commit(self):
        if not self._result_depth:
            return super().commit()


@contextmanager
def result_transaction(db):
    if not isinstance(db, ProjectConnection):
        raise TypeError('Для атомарного приёма результата требуется ProjectConnection')
    if db.in_transaction and not db._result_depth:
        raise ValueError('Приём результата требует завершения предыдущей транзакции')
    with _write_transaction(db):
        db._result_depth += 1
        try:
            yield
            if not db.in_transaction:
                raise ValueError('Транзакция результата была завершена до проверки хранения')
        finally:
            db._result_depth -= 1


def persist_execution(db,plan,request,context):
    """Compare-and-set pending + manifest + clarification in one write lock."""
    if context.get('resolved_task_request')!=request:raise ValueError('Не совпадает исходное задание исполнителя')
    from task_artifacts import store_pending_task
    with _write_transaction(db):
        validate_live_plan(db,plan.project_id,plan)
        context['task_id']=store_pending_task(db,plan.project_id,request,
            'Задание выполняется или требует продолжения.',context.get('revision_id'),
            task_id=context.get('task_id'),
            force_new=plan.action['operation'] in ('create_text','create_bundle','revise_saved'))
        db.execute('CREATE TABLE IF NOT EXISTS ai_semantic_tasks(project_id TEXT,request TEXT,context_json TEXT,PRIMARY KEY(project_id,request))')
        body=json.dumps(context,ensure_ascii=False)
        db.execute('INSERT OR REPLACE INTO ai_semantic_tasks VALUES(?,?,?)',(plan.project_id,request,body))
        db.execute('INSERT OR REPLACE INTO ai_task_contexts(task_id,project_id,context_json) VALUES(?,?,?)',
                   (context['task_id'],plan.project_id,body))
        # The originating dialogue remains resolving until task completion.
        if plan.snapshot.get('current_message_id'):
            from conversation_runtime import ConversationRuntime
            ConversationRuntime(db).link_execution_in_transaction(
                plan.project_id,plan.turn_id,context['task_id'])
        from question_registry import park_current_question,finish_paused_question
        if plan.action['source_id'].startswith('question:'):
            selected=plan.snapshot['sources'][plan.action['source_id']]
            finish_paused_question(db,plan.project_id,selected['question_id'],selected['request'],status='answered')
        elif plan.action['source_id']=='clarification':
            db.execute('DELETE FROM ai_dialogue_questions WHERE project_id=?',(plan.project_id,))
        else:
            park_current_question(db,plan.project_id)
        from dialogue_runtime import dialogue_snapshot
        claim = replace(plan, snapshot=dialogue_snapshot(db, plan.project_id,
            current_message_id=(plan.snapshot.get('current_message_id') or '').partition(':')[2] or None))
    return claim


def canonical_request(plan):
    a=plan.action;s=plan.snapshot
    base=plan.utterance if a['source_id']=='current' else s['sources'][a['source_id']]['request']
    if a['operation']=='answer_clarification':
        base+='\n\nОтвет пользователя на уточнение:\n'+plan.utterance
    elif a['operation']!='resume' and a['source_id']!='current' and a['include_current_instruction']:
        base+='\n\nПоследующее указание пользователя:\n'+plan.utterance
    references=[]
    for ident in a['message_ids']:
        item=s['messages'][ident]
        if item['role']!='user' or item['content']==base or item['content']==plan.utterance:continue
        label='Реплика пользователя' if item['role']=='user' else 'Производный контекст AI; не подтверждение фактов бизнеса'
        references.append(label+' ['+ident+']:\n'+item['content'])
    if references and a['operation']!='resume':base+='\n\nКонтекст, на который ссылается текущее поручение:\n'+'\n\n'.join(references)
    return base


def compile_execution(db,plan,workspace):
    validate_live_plan(db,plan.project_id,plan)
    a=plan.action;s=plan.snapshot;op=a['operation'];request=canonical_request(plan)
    if op not in ('create_bundle','create_text','revise_saved','resume','answer_clarification'):
        raise ValueError('Это действие не запускает текстовый исполнитель')
    kind=a['artifact_kind']
    if kind=='none':raise ValueError('Нет типа исполнителя')
    source_id=a['source_id']
    pending=s['sources'].get(source_id) if source_id=='pending' or source_id.startswith('paused:') else None
    project=s['project']
    context={'project':project['name'],'category':project['category'],'description':project['description'],
        'project_workspace':str(workspace),'resolved_task_request':request,'task_mode':'new',
        'semantic_execution_kind':kind,'semantic_authorized':True,'semantic_schedule':a['schedule'],
        'semantic_requested_media':a['requested_media'],'dialogue_turn_id':plan.turn_id,
        'task_facts':{},'existing_documents':[], 'verified_project_materials':[],
        'recent_messages':[], 'dialogue_references':[],
        'memory_policy':'Generated drafts are revision memory, never proof of business facts.'}
    if project['description']:
        context['verified_project_materials'].append({'source_type':'project_description','title':'Описание проекта',
            'content':project['description'],'verified':True})
    # Only user sources explicitly selected by the semantic decision can supply
    # additional project facts. Assistant messages are continuity, not evidence.
    for ident in a['message_ids']:
        m=s['messages'][ident]
        context['recent_messages'].append({'role':m['role'],'content':m['content']})
        if m['role']=='assistant':
            context['dialogue_references'].append({'id':ident,'role':'assistant','content':m['content'],
                'authority':'derived_dialogue_context_not_factual_evidence'})
            context['existing_documents'].append({'kind':'dialogue_context','title':ident,
                'content':m['content'],'authority':'derived_dialogue_context_not_factual_evidence'})
        if m['role']=='user' and m['content']!=request:
            context['verified_project_materials'].append({'source_type':'user_selected_context','title':ident,
                'content':m['content'],'verified':True})
    if op=='resume' and pending and pending.get('execution_kind'):
        if kind!=pending['execution_kind'] or a['schedule']!=pending.get('schedule') or a['requested_media']!=pending.get('requested_media'):
            raise ValueError('Продолжение должно сохранять параметры исходного задания')
    if op in ('resume','answer_clarification') and pending and pending.get('execution_kind'):
        from task_registry import task_context_json
        saved_context=task_context_json(db,plan.project_id,pending.get('task_id'))
        if saved_context is None and a['source_id']=='pending' and pending.get('legacy_origin'):
            record=db.execute('SELECT context_json FROM ai_semantic_tasks WHERE project_id=? AND request=?',
                              (plan.project_id,pending['request'])).fetchone()
            saved_context=record[0] if record else None
        if saved_context is None:raise ValueError('Нет сохранённого контракта незавершённого задания')
        original_context=json.loads(saved_context)
        if not isinstance(original_context,dict):raise ValueError('Повреждён контракт незавершённого задания')
        for field in ('revision_document','existing_documents','verified_project_materials','recent_messages','dialogue_references'):
            if field in original_context:
                expected=dict if field=='revision_document' else list
                if not isinstance(original_context[field],expected):raise ValueError('Повреждён сохранённый контекст: '+field)
                prior=copy.deepcopy(original_context[field])
                if op=='answer_clarification' and isinstance(prior,list):
                    current=context.get(field,[])
                    context[field]=prior+[v for v in current if v not in prior]
                else:context[field]=prior
    if op=='answer_clarification':
        question=s['sources'][a['source_id']]['question']
        context['clarification_question']=question;context['clarification_answer']=plan.utterance
        context['dialogue_references'].append({'id':a['source_id']+':question','role':'assistant',
            'content':question,'authority':'derived_dialogue_context_not_factual_evidence'})
    if op in ('resume','answer_clarification'):
        context['task_mode']='resume' if op=='resume' else 'clarification'
        if pending:
            if pending.get('task_id'):context['task_id']=pending['task_id']
            context['pending_original_request']=pending['request']
            context['clarification_question']=pending['question']
            if op=='answer_clarification':context['clarification_answer']=plan.utterance
            from task_artifacts import load_revision_seed,verified_document
            revision_id=pending.get('revision_id')
            if revision_id:
                seed=load_revision_seed(workspace,plan.project_id,pending['request'],revision_id)
                if request!=pending['request']:
                    # Changed instructions start a new isolated attempt; the old
                    # checkpoint remains intact and is only an unapproved seed.
                    seed=copy.deepcopy(seed);seed['request']=request;revision_id=uuid4().hex
                    if not a['reuse_structure']:
                        seed.update(posts={},slots=[],strategy='')
                    elif seed.get('scope')!=a['schedule']:
                        raise ValueError('Новый объём не совпадает со структурой сохранённой редакции')
                context.update(revision_seed=seed,revision_id=revision_id)
            elif request==pending['request']:
                saved=verified_document(db,plan.project_id,request,'strategy')
                context['resume_documents']=[{'kind':'strategy','content':saved,'already_saved':True}] if saved else []
                from task_checkpoint import checkpoint_path
                path=checkpoint_path(workspace,'pending_text',request,pending.get('task_id'))
                # The only authorized fallback to the old request-keyed draft
                # belongs to an explicitly migrated pre-registry assignment.
                if not path.exists() and pending.get('legacy_origin'):
                    path=checkpoint_path(workspace,'pending_text',request)
                try:
                    checkpoint=json.loads(path.read_text(encoding='utf-8'))
                    owned=(checkpoint.get('task_id')==pending['task_id'] if pending.get('task_id')
                           and not pending.get('legacy_origin') else checkpoint.get('task_id') in (None,pending.get('task_id')))
                    if (checkpoint.get('request')==request and owned and
                            checkpoint.get('request_sha256') in (None,hashlib.sha256(request.encode('utf-8')).hexdigest())):
                        # Only this registry-bound loader may migrate unsigned legacy checkpoints.
                        # The executor itself rejects any unsigned or mismatched external candidate.
                        context['pending_text_candidate']={k:checkpoint.get(k) for k in ('draft','issues','content_contract','quality_review')}
                        context['pending_text_candidate']['request_sha256']=hashlib.sha256(request.encode('utf-8')).hexdigest()
                except (OSError,ValueError,TypeError,AttributeError):pass
    if op=='revise_saved' or op=='answer_clarification' and a['target_ids']:
        docs=[s['documents'][i] for i in a['target_ids']]
        context['task_mode']='revision'
        bound={d['source_id'] for d in docs}
        if op=='answer_clarification' and len(bound)==1 and None not in bound:
            original_id=next(iter(bound))
            original=s['sources'][original_id]['request']
            if request!=original:request=original+'\n\nУточнение доработки:\n'+request
            context['resolved_task_request']=request
        context['existing_documents'].extend({**d,'authority':'prior_project_document_context_only'} for d in docs)
        if kind=='bundle':
            if len(docs)!=2 or {d['kind'] for d in docs}!={'strategy','calendar'}:
                raise ValueError('Комплект должен содержать выбранные стратегию и календарь')
            sources={d['source_id'] for d in docs}
            if len(sources)!=1 or None in sources or op=='revise_saved' and a['source_id'] not in sources:
                raise ValueError('Документы не связаны с одним подтверждённым исходным заданием')
            from task_artifacts import revision_seed
            original=s['sources'][next(iter(sources))]['request']
            seed=revision_seed(db,plan.project_id,original,docs[0]['id'],document_ids=a['target_ids'])
            # A changed plan must be replanned, not forced into old slots.
            if not a['reuse_structure']:
                seed.update(posts={},slots=[],strategy='')
            else:
                if seed['scope']!=a['schedule']:
                    raise ValueError('Объём выбранного календаря не совпадает с решением AI')
            seed['original_request']=original;seed['request']=request
            context.update(revision_seed=seed,revision_id=uuid4().hex)
        else:
            if len(docs)!=1 or docs[0]['kind']!=kind:raise ValueError('Не определён один документ нужного типа')
            context['revision_document']={'id':docs[0]['id'],'content':docs[0]['content'],
                'authority':'unapproved_revision_input_not_facts'}
    return request,context



def cancel_pending(db,plan):
    """Archive the full pending state and clear it atomically, preserving files."""
    if plan.action['operation']!='cancel_pending' or (plan.action['source_id'] not in ('pending','clarification') and not plan.action['source_id'].startswith(('paused:','question:'))):
        raise ValueError('Не выбрано незавершённое задание для отмены')
    source=plan.action['source_id'];state=copy.deepcopy(plan.snapshot['sources'][source])
    if source=='pending' or source.startswith('paused:'):
        from task_registry import task_context_json
        body=task_context_json(db,plan.project_id,state.get('task_id'))
        if body is not None:
            try:state['execution_context']=json.loads(body)
            except (ValueError,TypeError):state['raw_execution_context']=body
    from datetime import datetime,timezone
    with _write_transaction(db):
        validate_live_plan(db,plan.project_id,plan)
        db.execute('CREATE TABLE IF NOT EXISTS ai_cancelled_tasks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,request TEXT NOT NULL,state_json TEXT NOT NULL,cancelled_at TEXT NOT NULL)')
        db.execute('INSERT INTO ai_cancelled_tasks VALUES(?,?,?,?,?)',
            (plan.turn_id,plan.project_id,state['request'],json.dumps(state,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
        if source.startswith('question:'):
            from question_registry import finish_paused_question
            finish_paused_question(db,plan.project_id,state['question_id'],state['request'],status='cancelled')
            table=None
        elif source.startswith('paused:'):
            from task_lifecycle import TaskLifecycle
            TaskLifecycle(db).cancel_paused(plan.project_id,state['task_id'],state['request'])
            table=None
        elif source=='pending':
            from task_lifecycle import TaskLifecycle
            TaskLifecycle(db).finish(plan.project_id,state['request'],task_id=state['task_id'],status='cancelled')
            table=None
        else:
            table='ai_dialogue_questions'
            changed=db.execute('DELETE FROM '+table+' WHERE project_id=? AND request=?',(plan.project_id,state['request'])).rowcount
            if changed!=1:raise ValueError('Состояние задания изменилось перед отменой')
        if source=='pending':
            if db.execute('SELECT 1 FROM ai_pending_tasks WHERE project_id=?',(plan.project_id,)).fetchone():
                raise ValueError('Текущая задача снова появилась после удаления')
            question=plan.snapshot['sources'].get('clarification') or {}
            if (question.get('question_context') or {}).get('source_id')=='pending':
                db.execute('DELETE FROM ai_dialogue_questions WHERE project_id=?',(plan.project_id,))
        if table is not None and db.execute('SELECT 1 FROM '+table+' WHERE project_id=?',(plan.project_id,)).fetchone():
            raise ValueError('Не подтверждена отмена незавершённого задания')
    return {'project_id':plan.project_id,'cancelled_source':source,'archive_id':plan.turn_id,
            'request':state['request'],'archived':True,'draft_files_preserved':True}
