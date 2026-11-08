"""Semantic dialogue front door. User wording is interpreted only by the model.

The host issues resource IDs and validates typed actions; it never classifies
an utterance by words, phrases, topics, grammar, or language.
"""
from __future__ import annotations
from dataclasses import dataclass
import copy
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4
from durable_json import atomic_write_json

OPERATIONS=('respond','clarify','inspect','cancel_pending','create_bundle','create_text','revise_saved','resume',
            'answer_clarification','open_document','rename_document','project_control','image_edit','video_trim','local_media')
KINDS=('none','bundle','strategy','calendar','plans','tasks','analytics')
FIELDS=('operation','artifact_kind','target_ids','source_id','message_ids','reply','reason',
        'include_current_instruction','new_title','control_scope','use_reference','start','end','offset','length','schedule','requested_media','reuse_structure')


def digest(value):return hashlib.sha256(str(value).encode('utf-8')).hexdigest()


def dialogue_snapshot(db,project_id,*,selected_id=None,ui_context=None,current_message_id=None):
    row=db.execute('SELECT name,category,description FROM projects WHERE id=?',(project_id,)).fetchone()
    if not row:raise ValueError('Проект не найден')
    messages={};documents={};sources={'current':None}
    for ident,role,text,stamp in db.execute('SELECT id,role,content,created_at FROM messages WHERE project_id=? ORDER BY id',(project_id,)):
        messages['message:'+str(ident)]={'id':'message:'+str(ident),'role':role,'content':text,'created_at':stamp}
        if role=='user':sources['message:'+str(ident)]={'request':text,'origin':'user_message'}
    from task_artifacts import original_document_request
    for ident,kind,title,content,stamp in db.execute('SELECT id,kind,title,content,updated_at FROM manager_documents WHERE project_id=? ORDER BY updated_at,rowid',(project_id,)):
        try:request=original_document_request(db,project_id,ident)
        except Exception:request=None
        source='task:'+digest(request) if request else None
        if source:sources[source]={'request':request,'origin':'original_execution_receipt'}
        documents[ident]={'id':ident,'kind':kind,'title':title,'content':content,'sha256':digest(content),
            'updated_at':stamp,'source_id':source}
    db.execute('CREATE TABLE IF NOT EXISTS ai_dialogue_questions(project_id TEXT PRIMARY KEY,request TEXT NOT NULL,question TEXT NOT NULL,plan_json TEXT)')
    question=db.execute('SELECT request,question FROM ai_dialogue_questions WHERE project_id=?',(project_id,)).fetchone()
    if question:
        sources['clarification']={'request':question[0],'question':question[1],'origin':'dialogue_clarification','question_context_sha256':None}
        if 'plan_json' in {r[1] for r in db.execute('PRAGMA table_info(ai_dialogue_questions)')}:
            record=db.execute('SELECT plan_json FROM ai_dialogue_questions WHERE project_id=?',(project_id,)).fetchone()
            if record and record[0]:
                sources['clarification']['question_context_sha256']=digest(record[0])
                try:
                    parsed=json.loads(record[0])
                    if not isinstance(parsed,dict):raise ValueError('Некорректная связь вопроса с заданием')
                    sources['clarification']['question_context']=parsed
                except (ValueError,TypeError) as exc:sources['clarification']['question_context_error']=str(exc)
    from task_registry import paused_tasks
    paused = paused_tasks(db,project_id)
    from question_registry import paused_questions
    for q in paused_questions(db,project_id):
        sources['question:'+q['question_id']]={
            'request':q['request'],'question':q['question'],'question_id':q['question_id'],
            'origin':'suspended_clarification',
            'question_context_sha256':digest(q['plan_json']) if q['plan_json'] else None}
    pending=db.execute('SELECT request,question,revision_id FROM ai_pending_tasks WHERE project_id=?',(project_id,)).fetchone()
    active=db.execute("SELECT task_id,legacy_origin FROM ai_task_registry WHERE project_id=? AND status='active'",
                      (project_id,)).fetchone()
    if pending:
        sources['pending']={'request':pending[0],'question':pending[1],'revision_id':pending[2],
                            'origin':'pending_task','task_id':active[0] if active else None,'legacy_origin':bool(active[1]) if active else False,'execution_context_sha256':None}
    for item in paused:
        sources['paused:'+item['task_id']]={'request':item['request'],'question':item['question'],
                'revision_id':item['revision_id'],'origin':'paused_task','task_id':item['task_id'],'legacy_origin':bool(item['legacy_origin']),
                'execution_context_sha256':None}
    for name,source in list(sources.items()):
        if name!='pending' and not name.startswith('paused:'):continue
        from task_registry import task_context_json
        body=task_context_json(db,project_id,source.get('task_id'))
        # The compatibility table is safe only for the legacy active pointer;
        # paused tasks require their own immutable task-scoped contract.
        if body is None and name=='pending' and source.get('legacy_origin') and db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_semantic_tasks'").fetchone():
            record=db.execute('SELECT context_json FROM ai_semantic_tasks WHERE project_id=? AND request=?',
                              (project_id,source['request'])).fetchone()
            body=record[0] if record else None
        if body is None and source.get('task_id') and not source.get('legacy_origin'):
            # A newly registered task must never inherit the compatibility
            # context of another task with the same request string.
            source['execution_contract_error']='Нет отдельного контракта задания'
        if body is not None:
            source['execution_context_sha256']=digest(body)
            try:
                manifest=json.loads(body)
                if not isinstance(manifest,dict):raise ValueError('Повреждён сохранённый контракт задания')
                if manifest.get('semantic_authorized') is not True:
                    raise ValueError('Сохранённый контракт не подтверждает тип исполнителя')
                from task_semantics import semantic_parameters
                parameters=semantic_parameters(manifest)
                source['execution_kind']=parameters['kind']
                source['schedule']=parameters['schedule']
                source['requested_media']=parameters['requested_media']
            except (ValueError,TypeError,KeyError) as exc:
                source['execution_contract_error']=str(exc)
    current='message:'+str(current_message_id) if current_message_id is not None else None
    if current and (current not in messages or messages[current]['role']!='user'):
        raise ValueError('Не найдено исходное сообщение текущего диалога')
    return {'schema_version':1,'project_id':project_id,'current_message_id':current,'project':dict(zip(('name','category','description'),row)),
        'documents':documents,'messages':messages,'sources':sources,'selected_id':selected_id if selected_id in documents else None,
        'ui_context':dict(ui_context or {}),'media':__import__('project_media_sources').media_catalog(db,project_id)}


@dataclass(frozen=True)
class DialoguePlan:
    project_id: str
    utterance: str
    turn_id: str
    snapshot: dict
    action: dict
    normalizations: tuple = ()


class DialogueContract:
    def __init__(self,message,snapshot,turn_id=None):
        if not isinstance(message,str) or not message.strip():raise ValueError('Пустая реплика')
        self.message=message;self.snapshot=copy.deepcopy(snapshot);self.turn_id=turn_id or uuid4().hex
        current=self.snapshot.get('current_message_id')
        if current and self.snapshot['messages'][current]['content']!=message:
            raise ValueError('Текущая реплика не совпадает с сохранённым исходным сообщением')
    def available_operations(self):
        """Resource/capability preconditions only; never inspect the utterance."""
        snap=self.snapshot;allowed={'respond','clarify','project_control'}
        if snap['messages'] or snap['documents'] or len(snap['sources'])>1:allowed.add('inspect')
        allowed.update(('create_bundle','create_text'))
        if snap['documents']:allowed.update(('revise_saved','open_document','rename_document'))
        if any(k=='pending' or k.startswith('paused:') for k in snap['sources'] if snap['sources'][k] and not snap['sources'][k].get('execution_contract_error')):allowed.add('resume')
        if any(key in snap['sources'] for key in ('pending','clarification')) or any(k.startswith(('paused:','question:')) for k in snap['sources']):allowed.add('cancel_pending')
        if 'clarification' in snap['sources'] or any(k.startswith('question:') for k in snap['sources']) or any((k=='pending' or k.startswith('paused:')) and not snap['sources'][k].get('execution_contract_error') for k in snap['sources']):
            allowed.add('answer_clarification')
        ui=snap.get('ui_context') or {};caps=ui.get('capabilities') or {}
        source=ui.get('source_path');suffix=Path(source).suffix.lower() if isinstance(source,str) else ''
        if suffix in ('.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff') and any(caps.get(key) for key in ('image_local_legacy','image_cloud_optional','advanced_local_image_edit')):
            allowed.add('image_edit')
        if suffix in ('.mp4','.mov','.mkv','.avi','.webm') and caps.get('video_trim_ffmpeg') is True:allowed.add('video_trim')
        from local_media_contract import available_local_tasks
        if available_local_tasks(snap.get('media',{}),caps):
            allowed.add('local_media')
        return tuple(op for op in OPERATIONS if op in allowed)

    def schema(self):
        snap=self.snapshot;ids=list(snap['documents']);message_ids=list(snap['messages'])
        result={'type':'object','additionalProperties':False,'required':list(FIELDS),'properties':{
            'operation':{'type':'string','enum':list(self.available_operations())},
            'artifact_kind':{'type':'string','enum':list(KINDS),
                'description':'Раздел отдельного создаваемого текста; create_bundle уже определяет комплект, его kind выводит программа.'},
            'target_ids':{'type':'array','items':({'type':'string','enum':ids} if ids else {'type':'string'}),'maxItems':len(ids)},
            'source_id':{'type':'string','enum':list(snap['sources'])},
            'message_ids':{'type':'array','items':({'type':'string','enum':message_ids} if message_ids else {'type':'string'}),'maxItems':len(message_ids)},
            'reply':{'type':'string'},'reason':{'type':'string','minLength':8},
            'include_current_instruction':{'type':'boolean'},'new_title':{'type':'string'},
            'control_scope':{'type':'string','enum':['none','documents','project','assets','chat']},
            'use_reference':{'type':'boolean'},'start':{'type':['number','null']},'end':{'type':['number','null']},
            'schedule':{'type':['object','null'],'additionalProperties':False,'required':['weeks','per_week','total'],
                'properties':{'weeks':{'type':'integer','minimum':1,'maximum':26},'per_week':{'type':'integer','minimum':1,'maximum':7},'total':{'type':'integer','minimum':1,'maximum':182}}},
            'requested_media':{'type':'boolean','description':'Нужны ли отдельные готовые медиафайлы как результат. Текст, сценарии, раскадровка и описание визуала являются текстовыми документами, а не отрендеренными файлами.'},'reuse_structure':{'type':'boolean'},
            'offset':{'type':'integer','minimum':0},'length':{'type':'integer','minimum':1,'maximum':20000}}}
        if 'local_media' in self.available_operations():
            from local_media_contract import local_media_schema
            result['required'].append('media')
            result['properties']['media']=local_media_schema(snap.get('media',{}),snap.get('ui_context',{}).get('capabilities',{}))
        return result
    def context(self):
        s=self.snapshot
        return {'project_id':s['project_id'],'project':s['project'],'selected_document_id':s['selected_id'],
            'available_tools':{name:TOOL_DESCRIPTIONS[name] for name in self.available_operations()},
            'documents':[{k:v for k,v in d.items() if k!='content'}|{'characters':len(d['content'])} for d in s['documents'].values()],
            'conversation':[{'id':m['id'],'role':m['role'],'created_at':m['created_at'],
                'content':m['content'][:(1600 if index>=len(s['messages'])-12 else 160)],
                'characters':len(m['content']),
                'excerpt_only':len(m['content'])>(1600 if index>=len(s['messages'])-12 else 160)}
                for index,m in enumerate(s['messages'].values())],
            'media_sources':[{key:item[key] for key in ('id','label','kind')} for item in s.get('media',{}).values()],
            'request_sources':{key:({**value,'request':value['request'][:1600],
                'request_characters':len(value['request']),'excerpt_only':len(value['request'])>1600} if value else None)
                for key,value in s['sources'].items()},'ui':s['ui_context']}
    def validate(self,value):
        if not isinstance(value,dict) or set(value) not in (set(FIELDS),set(FIELDS)|{'media'}):raise ValueError('Неполный структурированный план диалога')
        value=copy.deepcopy(value)
        s=self.snapshot;op=value['operation'];normalizations=[]
        if op!='local_media':
            if value.get('media') is not None:raise ValueError('Media parameters belong only to the selected media tool')
            value.pop('media',None)
        if any(not isinstance(value[k],str) for k in ('operation','artifact_kind','source_id')) or op not in OPERATIONS or value['artifact_kind'] not in KINDS or value['source_id'] not in s['sources']:
            raise ValueError('Неизвестное действие или источник задания')
        if op not in self.available_operations():raise ValueError('Действие недоступно в текущем состоянии проекта: '+op)
        # An operation's fixed output type is host-owned protocol data. This
        # does not infer or change the user's intent; the model chose the tool.
        fixed_kind={'create_bundle':'bundle'}.get(op)
        targets=value.get('target_ids')
        if (op in ('revise_saved','answer_clarification') and isinstance(targets,list) and targets
                and all(isinstance(i,str) and i in s['documents'] for i in targets)):
            kinds=[s['documents'][i]['kind'] for i in targets]
            if len(kinds)==1:fixed_kind=kinds[0]
            elif len(kinds)==2 and set(kinds)=={'strategy','calendar'}:fixed_kind='bundle'
            else:raise ValueError('Выберите один документ или связанную пару стратегия/календарь')
        if op=='resume' and value['source_id'] in s['sources'] and value['source_id']!='current':
            pending=s['sources'][value['source_id']]
            if pending.get('execution_contract_error'):
                raise ValueError('Контракт выбранного задания повреждён: продолжение заблокировано')
            if pending.get('execution_kind'):
                fixed_kind=pending['execution_kind']
                for field,origin in (('schedule','schedule'),('requested_media','requested_media')):
                    if value.get(field)!=pending.get(origin):
                        normalizations.append({'field':field,'from':value.get(field),'to':pending.get(origin),
                            'basis':'immutable_pending_execution_contract'})
                        value[field]=copy.deepcopy(pending.get(origin))
        if fixed_kind is not None and fixed_kind not in KINDS:raise ValueError('Неизвестный тип выбранного документа')
        if fixed_kind is not None and value['artifact_kind']!=fixed_kind:
            normalizations.append({'field':'artifact_kind','from':value['artifact_kind'],'to':fixed_kind,
                'basis':'fixed_output_type_of_selected_operation'})
            value['artifact_kind']=fixed_kind
        for field,catalog in (('target_ids',s['documents']),('message_ids',s['messages'])):
            values=value[field]
            if not isinstance(values,list) or any(not isinstance(v,str) for v in values) or len(values)!=len(set(values)) or any(v not in catalog for v in values):
                raise ValueError('Действие ссылается на невыданный объект')
        if any(not isinstance(value[k],str) for k in ('reply','reason','new_title')) or len(value['reason'].strip())<8:
            raise ValueError('Нет обоснованного решения AI')
        if any(type(value[k]) is not bool for k in ('include_current_instruction','use_reference','requested_media','reuse_structure')):
            raise ValueError('Не определены параметры действия')
        if not isinstance(value['control_scope'],str) or value['control_scope'] not in ('none','documents','project','assets','chat'):raise ValueError('Неизвестная область операции')
        for k in ('start','end'):
            if value[k] is not None and (type(value[k]) not in (float,int) or not math.isfinite(value[k]) or value[k]<0):raise ValueError('Некорректная граница видео')
        if type(value['offset']) is not int or value['offset']<0 or type(value['length']) is not int or not 1<=value['length']<=20000:
            raise ValueError('Некорректный диапазон чтения')
        schedule=value['schedule']
        if schedule is not None:
            if (not isinstance(schedule,dict) or set(schedule)!={'weeks','per_week','total'}
                    or any(type(v) is not int for v in schedule.values())
                    or not 1<=schedule['weeks']<=26 or not 1<=schedule['per_week']<=7
                    or schedule['total']!=schedule['weeks']*schedule['per_week']):
                raise ValueError('Несогласованный объём календаря')
        targets=value['target_ids']
        if op in ('respond','clarify') and not value['reply'].strip():raise ValueError('Нет ответа пользователю')
        if op in ('open_document','rename_document') and len(targets)!=1:raise ValueError('Нужно выбрать один документ')
        if op=='rename_document' and not value['new_title'].strip():raise ValueError('Не предложено название')
        if op=='cancel_pending' and value['source_id'] not in ('pending','clarification') and not value['source_id'].startswith(('paused:','question:')):
            raise ValueError('Для отмены выберите источник pending или clarification')
        if op=='revise_saved' and not targets:raise ValueError('Не выбраны документы для доработки')
        if op=='revise_saved':
            bound={s['documents'][ident]['source_id'] for ident in targets}
            if None not in bound and (len(bound)!=1 or value['source_id'] not in bound):
                raise ValueError('Доработка должна сохранять подтверждённое исходное задание выбранных документов')
        if op in ('resume','answer_clarification','revise_saved') and value['artifact_kind']=='none':raise ValueError('Не определён исполнитель')
        if op in ('resume','answer_clarification','revise_saved') and value['artifact_kind']=='bundle' and schedule is None:raise ValueError('Не определён объём комплекта')
        if op=='resume' and value['source_id']!='pending' and not value['source_id'].startswith('paused:'):raise ValueError('Не выбрано незавершённое задание')
        if op=='answer_clarification' and value['source_id'] not in ('pending','clarification') and not value['source_id'].startswith(('paused:','question:')):raise ValueError('Не выбран заданный вопрос')
        if op=='answer_clarification' and (value['source_id']=='pending' or value['source_id'].startswith('paused:')) and s['sources'][value['source_id']].get('execution_contract_error'):
            raise ValueError('Нельзя продолжить повреждённый контракт через ответ на уточнение')
        if op=='create_bundle' and schedule is None:raise ValueError('Для выбранного create_bundle не заполнен параметр schedule')
        if op in ('create_bundle','create_text') and not (value['source_id']=='current' or (
                value['source_id'].startswith('message:') and
                s['messages'][value['source_id']]['role']=='user')):
            raise ValueError('Новое задание должно происходить из выбранного сообщения пользователя')
        if op=='create_text' and value['artifact_kind'] in ('none','bundle'):raise ValueError('Не указан раздел нового документа')
        if (op in ('create_bundle','create_text','resume','answer_clarification','revise_saved')
                and value['requested_media']):
            raise ValueError('Выбранный текстовый исполнитель создаёт документы, но не готовые медиафайлы. '
                'Повторно определи требуемый результат по исходному диалогу. Для текстовых материалов '
                'requested_media=false; если нужны именно файлы или это неоднозначно, выбери clarify '
                'и объясни отсутствующую возможность. Не изменяй согласованные параметры молча.')
        if op=='project_control' and (value['control_scope']=='none' or value['control_scope']=='documents' and not targets):
            raise ValueError('Не определён объект подтверждаемой операции')
        if op=='inspect' and not (targets or value['message_ids'] or value['source_id']!='current'):raise ValueError('Не выбран контекст для чтения')
        if op=='inspect':
            resources=[s['documents'].get(i) or s['messages'].get(i) for i in targets+value['message_ids']]
            texts=[item['content'] for item in resources]
            if value['source_id']!='current':texts.append(s['sources'][value['source_id']]['request'])
            if any(value['offset']>len(text) for text in texts):raise ValueError('Диапазон чтения вне выбранного ресурса')
        if op=='video_trim' and (value['start'] is None or value['end'] is None or value['start']>=value['end']):raise ValueError('Не указан полный интервал монтажа')
        if op=='local_media':
            media=value.get('media')
            local_media_request(s,media)
            if value['artifact_kind']!='none':
                normalizations.append({'field':'artifact_kind','from':value['artifact_kind'],'to':'none','basis':'local_media_is_not_a_text_artifact'})
                value['artifact_kind']='none'
            if value['requested_media'] is not True:
                normalizations.append({'field':'requested_media','from':False,'to':True,'basis':'selected_local_media_operation'})
                value['requested_media']=True
        return DialoguePlan(s['project_id'],self.message,self.turn_id,s,dict(value),tuple(normalizations))
    def inspect(self,plan):
        result=[]
        for ident in plan.action['target_ids']+plan.action['message_ids']:
            resource=self.snapshot['documents'].get(ident) or self.snapshot['messages'].get(ident)
            body=resource['content'];start=plan.action['offset'];end=min(len(body),start+plan.action['length'])
            if start>len(body):raise ValueError('Диапазон вне текста')
            result.append({'id':ident,'role':resource.get('role','derived_document_not_factual_authority'),
                'text':body[start:end],'start':start,'end':end,'total_characters':len(body),
                'complete':start==0 and end==len(body)})
        if plan.action['source_id']!='current':
            ident=plan.action['source_id'];body=self.snapshot['sources'][ident]['request']
            start=plan.action['offset'];end=min(len(body),start+plan.action['length'])
            if start>len(body):raise ValueError('Диапазон вне исходного задания')
            result.append({'id':ident,'role':'user_source','text':body[start:end],'start':start,'end':end,
                'total_characters':len(body),'complete':start==0 and end==len(body)})
        return result


TOOL_DESCRIPTIONS={
 'respond':'Ответить в диалоге. Никакие материалы не изменяются; не сообщай о неисполненных операциях.',
 'clarify':'Задать необходимый вопрос, если намерение или объект действительно неоднозначны.',
 'inspect':'Прочитать выданные документы, сообщения или source_id по ID/range, если для ответа нужен полный контекст.',
 'cancel_pending':'Отменить выбранное pending-задание или вопрос уточнения, сохранив исходное поручение, черновики и документы. source_id=pending или clarification.',
 'create_bundle':'Создать НОВУЮ стратегию и календарь по текущему запросу. Существующие незавершённые задания сохраняются отдельно.',
 'create_text':'Создать НОВЫЙ текстовый документ по текущему запросу; прежние незавершённые задания не удалять.',
 'revise_saved':'Доработать один сохранённый документ или связанную пару стратегия/календарь по исходному заданию; старые версии сохраняются.',
 'resume':'Продолжить явно выбранную активную либо приостановленную задачу с её собственным исходным заданием и контрольными точками.',
 'answer_clarification':'Применить ответ к вопросу pending-задачи и продолжить её.',
 'open_document':'Открыть выбранный документ.', 'rename_document':'Переименовать выбранный документ.',
 'project_control':'Запросить подтверждаемое удаление выбранных объектов текущего проекта. Сам AI ничего не удаляет.',
 'image_edit':'Передать текущую инструкцию выбранному графическому инструменту. use_reference задаёт необходимость выбранного эталона.',
 'video_trim':'Передать выбранное видео с явно определённым интервалом инструменту монтажа.',
 'local_media':'Выполнить подтверждённую локальную операцию с media_sources по их ID: монтаж, обрезка, формат, ролик из фото или размер изображения. Заполни media; не подставляй пути, не называй это генеративной стилизацией.'}


def local_media_request(snapshot,media):
    from local_media_contract import available_local_tasks,validate_local_request
    fields={'task','source_ids','width','height','start','end','seconds_per_image'}
    if not isinstance(media,dict) or set(media)!=fields:raise ValueError('Full structured local media parameters required')
    task=media['task'];ids=media['source_ids'];catalog=snapshot.get('media',{})
    caps=(snapshot.get('ui_context') or {}).get('capabilities') or {}
    if not isinstance(task,str) or task not in available_local_tasks(catalog,caps):
        raise ValueError('Local media executor is not verified available')
    if not isinstance(ids,list) or not ids or any(not isinstance(ident,str) or ident not in catalog for ident in ids) or len(ids)!=len(set(ids)):
        raise ValueError('Local media uses only issued unique source IDs')
    expected='image' if task in ('image_resize','video_from_images') else 'video'
    if any(catalog[ident]['kind']!=expected for ident in ids):raise ValueError('Media source kind does not match selected executor')
    request={'task':task,'sources':[catalog[ident]['path'] for ident in ids],
             'expected_sha256':[catalog[ident].get('sha256') for ident in ids]}
    allowed={'width','height'}|({'start','end'} if task=='video_cut' else {'seconds_per_image'} if task=='video_from_images' else set())
    for key in fields-{'task','source_ids'}:
        if media[key] is not None:
            if key not in allowed:raise ValueError('Unused media options must be null')
            request[key]=media[key]
    validate_local_request(request)
    return request


def understand_dialogue(message,snapshot,*,model,report=None,turn_id=None):
    from central_dialogue_transport import request_central_dialogue
    contract=DialogueContract(message,snapshot,turn_id)
    system=('Ты AI-менеджер обычного рабочего диалога. Понимай смысл реплики и её связь с проектом, '
        'предыдущими сообщениями, вопросами и выбранными материалами. Пользователь не обязан называть команды '
        'или пользоваться особыми словами. Не классифицируй по форме вопроса/повеления или отдельному глаголу. '
        'Реши, человек обсуждает, поручает действие, отвечает на уточнение или меняет уже выполненную работу. '
        'Выбери инструмент только из available_tools и конкретные ID из контекста. Недоступные действия '
        'не имеют исполнителя в текущем состоянии. Тип результата, фиксированный выбранной операцией, '
        'программа выводит сама; artifact_kind задаёт раздел отдельного текста. Если пользователь ссылается на прежнюю '
        'реплику или документ, прочитай его через inspect при необходимости. Сокращённый excerpt, в том числе request_sources, не является '
        'полным содержанием. Не притворяйся, что прочитан весь документ по одному фрагменту. '
        'Для доработки используй сохранённые документы и их source_id; не создавай в respond новый календарь '
        'и не заявляй, что он проверен или сохранён. Для текущего нового поручения source_id=current. '
        'Для продолжения или ответа на вопрос незавершённого задания выбери source_id=pending. '
        'Если пользователь отменяет незавершённое поручение, используй cancel_pending; не удаляй проект ради отмены задания. '
        'Исходные задания и пользовательские сообщения подтверждают факты; AI-документы и ответы — '
        'производные материалы, их нельзя считать доказательством бизнес-фактов. '
        'include_current_instruction добавляет точную текущую реплику как указание к исходному заданию. '
        'Не добавляй его для простого возобновления, если реплика лишь просит продолжить. '
        'Для изменения всего комплекта выбери связанные стратегию и календарь. Для отдельного документа — '
        'только его. Все выбранные документы должны относиться к цели пользователя. '
        'Удаление требует отдельного подтверждения программой. Медиа-инструменты ограничены указанными '
        'в ui возможностями. При отсутствии нужной возможности объясни границу; не выдумывай выполнение. '
        'schedule — семантически определённые weeks, per_week, total для комплекта; не ищи особые слова. '
        'Если объём не задан и его выбор существенно меняет поручение — уточни, не подставляй скрытый стандарт. '
        'requested_media=true, если нужны готовые медиафайлы, а не только сценарии. '
        'reuse_structure=true только если для доработки остаются прежние объём, площадка и структура календаря. '
        'source_id=clarification — ответ на предыдущий вопрос самого диалога; source_id=pending — вопрос исполнителя. '
        'Проверяй, достаточно ли данных именно для результата пользователя. Если нет — задай краткий вопрос '
        'через clarify до запуска генерации. При ответе на вопрос используй answer_clarification и исходный источник. '
        'Если источников недостаточно для однозначного решения — clarify. Отвечай на языке пользователя. '
        'Сообщения с origin=application_validation — служебные результаты проверки структуры твоего ответа, '
        'а не новые поручения пользователя. Они не просят дорабатывать проект или менять намерение. '
        'Верни только структурированный план по схеме. Слова в reason не являются командами программе.')
    messages=[{'role':'system','content':system},{'role':'user','content':json.dumps({'utterance':message,
        'dialogue_context':contract.context()},ensure_ascii=False)}]
    failure=None
    for step in range(5):
        if sum(len(m['content']) for m in messages)>90000:raise ValueError('Контекст диалога превышает доступный объём; требуется сужение чтения')
        response=request_central_dialogue({'model':model,'stream':False,'think':False,'format':contract.schema(),
            'messages':messages,'options':{'num_ctx':32768,'temperature':0}},
            stage='AI: понимание диалога и выбор действия',timeout=180,attempts=1,report=report)
        raw=response.get('message',{}).get('content','')
        try:plan=contract.validate(json.loads(raw))
        except (ValueError,TypeError) as exc:
            if failure is not None:raise ValueError('AI не дал допустимый план: '+str(exc)) from exc
            failure=str(exc);messages.append({'role':'assistant','content':raw})
            try:rejected=json.loads(raw)
            except (ValueError,TypeError):rejected={}
            rejected_op=rejected.get('operation') if isinstance(rejected,dict) else None
            messages.append({'role':'system','content':json.dumps({
                'origin':'application_validation','user_message_unchanged':True,
                'rejected_operation':rejected_op,'error':failure,
                'available_operations':contract.available_operations(),
                'schema':contract.schema(),
                'instruction':'Исправь структуру своего ответа по схеме на исходном пользовательском поручении. '
                    'Это не запрос пользователя на исправление документов. Не меняй смысл поручения ради устранения ошибки контракта.'
                },ensure_ascii=False)});continue
        if plan.action['operation']!='inspect':return plan
        messages.append({'role':'assistant','content':raw})
        messages.append({'role':'user','content':json.dumps({'tool_result':'inspect','resources':contract.inspect(plan)},ensure_ascii=False)})
    raise ValueError('AI не завершил выбор действия после чтения контекста')


def save_turn(workspace,turn_id,**state):
    if not isinstance(turn_id,str) or len(turn_id)!=32 or any(ch not in '0123456789abcdef' for ch in turn_id):
        raise ValueError('Некорректный идентификатор реплики')
    path=Path(workspace)/'drafts'/('dialogue_turn_'+turn_id+'.json')
    old=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    atomic_write_json(path,{**old,**state,'turn_id':turn_id})
