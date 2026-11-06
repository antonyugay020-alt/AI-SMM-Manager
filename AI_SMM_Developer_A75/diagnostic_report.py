"""One self-describing support ZIP: scoped DB snapshot, full text, traces and runtime.

No model generation, paid requests, source changes or project database writes.
Credentials are redacted from textual diagnostics; binary assets are inventoried.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
import threading
import time
import traceback
import urllib.request
from uuid import uuid4
import zipfile

_TRACE=ContextVar('smm_support_trace',default=None)
_LOCK=threading.RLock()
_TEXT={'.py','.cmd','.bat','.json','.jsonl','.log','.txt','.md','.csv','.tsv','.yaml','.yml','.toml'}
_SECRET_FIELD=re.compile(r'(?:api[_ -]?key|(?:access|refresh|auth|bearer|id)[_-]?token|(?:^|[_ -])token$|password|passwd|secret|authorization|cookie|credential)',re.I)
_ENV_FIELDS=('AI_SMM_MODEL','AI_SMM_CLOUD_MODELS','AI_SMM_ENABLE_OPENAI_API',
 'AI_SMM_ENABLE_OPENAI_ACCEPTANCE','AI_SMM_TEST_PROFILE','AI_SMM_ENABLE_FREE_CLOUD_ACCEPTANCE')


def utc_now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class Redactor:
    def __init__(self,environ=None):
        env=os.environ if environ is None else environ
        self.secrets=sorted({str(v) for k,v in env.items() if _SECRET_FIELD.search(k) and v and len(str(v))>=6},key=len,reverse=True)
        self.replacements=0

    def text(self,value):
        out=str(value)
        for secret in self.secrets:
            self.replacements+=out.count(secret);out=out.replace(secret,'[REDACTED]')
        patterns=[r'\bsk-[A-Za-z0-9_-]{8,}',r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*',
            r'''(?i)(\b(?:[A-Z0-9_]*API[_ -]?KEY|access[_-]?token|refresh[_-]?token|password|passwd|client[_-]?secret|authorization)\b["']?\s*[:=]\s*)(["']?)([^\s,"';}\]]+)(\2)''',
            r'(?i)(https?://)[^/\s:@]+:[^/\s@]+@']
        for i,pattern in enumerate(patterns):
            def replace(m,index=i):
                self.replacements+=1
                if index==2:return m.group(1)+'"[REDACTED]"'
                if index==3:return m.group(1)+'[REDACTED]@'
                return '[REDACTED]'
            out=re.sub(pattern,replace,out)
        return out

    def value(self,value):
        if isinstance(value,dict):
            return {self.text(k):('[REDACTED]' if _SECRET_FIELD.search(str(k)) and value[k] is not None
                    and not isinstance(value[k],bool) else self.value(value[k])) for k in value}
        if isinstance(value,(list,tuple)):return [self.value(v) for v in value]
        if isinstance(value,bytes):return {'binary_bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()}
        if isinstance(value,str):
            # JSON stored in a SQLite text column may contain nested credentials.
            if value.lstrip().startswith(('{','[')):
                try:return json.dumps(self.value(json.loads(value)),ensure_ascii=False)
                except (ValueError,TypeError,RecursionError):pass
            return self.text(value)
        if value is None or isinstance(value,(bool,int,float)):return value
        return self.text(value)


def append_project_event(workspace,event,details=None):
    """Best-effort telemetry must never change a task's verdict or retry policy."""
    try:
        r=Redactor();record={'time':utc_now(),'event':event,**(details or {})}
        scope=_TRACE.get()
        if scope:record.setdefault('run_id',scope['run_id'])
        path=Path(workspace)/'drafts'/'application_events.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
        with _LOCK,path.open('a',encoding='utf-8') as handle:
            handle.write(json.dumps(r.value(record),ensure_ascii=False,default=str)+'\n')
        return True
    except Exception:return False


@contextmanager
def capture_task(workspace,request):
    scope={'workspace':str(workspace),'run_id':uuid4().hex,'request_sha256':hashlib.sha256(str(request).encode()).hexdigest()}
    token=_TRACE.set(scope)
    append_project_event(workspace,'task_capture_started',{'request':request,**scope})
    try:yield scope
    except Exception:
        append_project_event(workspace,'task_exception',{'traceback':traceback.format_exc()})
        raise
    finally:
        append_project_event(workspace,'task_capture_ended',scope)
        _TRACE.reset(token)


def record_model_event(event,**details):
    scope=_TRACE.get()
    if scope:append_project_event(scope['workspace'],event,details)


def snapshot_project(db,project_id,ui_state=None):
    """Read on the UI's SQLite thread; the background writer receives plain data."""
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'projects' not in tables:raise ValueError('База проектов недоступна')
    row=db.execute('SELECT * FROM projects WHERE id=?',(project_id,))
    found=row.fetchone()
    if not found:raise ValueError('Выбранный проект не найден')
    project=dict(zip([c[0] for c in row.description],found));data={};warnings=[]
    for table in sorted(tables):
        quoted='"'+table.replace('"','""')+'"'
        columns=[r[1] for r in db.execute('PRAGMA table_info('+quoted+')')]
        if 'project_id' not in columns or _SECRET_FIELD.search(table):continue
        try:
            cursor=db.execute('SELECT * FROM '+quoted+' WHERE project_id=?',(project_id,))
            names=[c[0] for c in cursor.description]
            data[table]=[dict(zip(names,r)) for r in cursor.fetchall()]
        except Exception as exc:warnings.append({'table':table,'error':str(exc)})
    return {'captured_at':utc_now(),'project':project,'tables':data,'ui':dict(ui_state or {}),'warnings':warnings}


def ollama_inventory():
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=2) as response:
            return {'status':'available','response':json.load(response)}
    except Exception as exc:return {'status':'unavailable','error':str(exc)}


def _files(root,errors=None):
    """Never follow symlinks or Windows directory junctions."""
    if not root.is_dir():return
    def failed(exc):
        if errors is not None:errors.append({'reason':'directory_read_error','error':str(exc)})
    for folder,dirs,files in os.walk(root,followlinks=False,onerror=failed):
        retained=[]
        for name in sorted(dirs):
            path=Path(folder)/name
            if (name in ('.venv','venv','__pycache__','.git','support_reports') or name.startswith('.env')
                    or path.is_symlink() or getattr(path,'is_junction',lambda:False)()):
                yield path
            else:retained.append(name)
        dirs[:]=retained
        for name in sorted(files):yield Path(folder)/name


def build_report(snapshot,workspace,studio_root,source_root,target,*,environ=None):
    """Atomically write all readable text; omissions/changes are explicit, not PASS."""
    workspace,studio_root,source_root,target=map(Path,(workspace,studio_root,source_root,target))
    r=Redactor(environ);entries={};inventory=[];warnings=list(snapshot.get('warnings',[]));written=set()
    trace_coverage={'task_capture_started':0,'model_call_started':0,'model_call_completed':0,'model_call_failed':0,'last_model_event_at':None}
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tempname=tempfile.mkstemp(prefix='.smm_report_',suffix='.tmp',dir=target.parent);os.close(fd)
    def safe_name(name):
        name=r.text(name).replace('\\','/')
        if name.startswith('/') or any(x in ('..','') for x in name.split('/')) or ':' in name:raise ValueError('Небезопасный путь в отчёте')
        return name
    try:
        with zipfile.ZipFile(tempname,'w',compression=zipfile.ZIP_DEFLATED) as z:
            def write(name,text,*,sanitized=False):
                name=safe_name(name)
                if name in written:raise ValueError('Повтор файла отчёта: '+name)
                payload=(text if sanitized else r.text(text)).encode('utf-8');z.writestr(name,payload);written.add(name)
                entries[name]={'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
            def obj(name,value):write(name,json.dumps(r.value(value),ensure_ascii=False,indent=2,default=str),sanitized=True)
            obj('project/snapshot.json',snapshot)
            for i,doc in enumerate(snapshot['tables'].get('manager_documents',[]),1):
                obj(f'documents/{i:04d}_metadata.json',{k:v for k,v in doc.items() if k!='content'})
                write(f'documents/{i:04d}.md',str(doc.get('content','')))
            history=['# История диалога\n']
            for m in snapshot['tables'].get('messages',[]):
                history.append('\n## '+str(m.get('created_at',''))+' · '+str(m.get('role',''))+'\n\n'+str(m.get('content',''))+'\n')
            write('project/conversation.md','\n'.join(history))
            if snapshot.get('ui',{}).get('editor_content'):
                write('project/current_editor.md',snapshot['ui']['editor_content'])
            roots=[('workspace',workspace),('runtime/application_logs',studio_root/'logs'),('installation/source',source_root)]
            for label,root in roots:
                if not root.is_dir():warnings.append({'scope':label,'reason':'directory_not_found'})
                for p in _files(root,warnings):
                    rel=p.relative_to(root).as_posix();item={'scope':label,'path':rel}
                    if p.resolve() in (target.resolve(),Path(tempname).resolve()):continue
                    try:
                        if p.is_symlink() or getattr(p,'is_junction',lambda:False)():
                            item['included']=False;item['reason']='link';inventory.append(item);continue
                        if p.is_dir():
                            item.update(included=False,reason='excluded_cache_environment_or_report_directory');inventory.append(item);continue
                        before=p.stat();item['bytes']=before.st_size
                        if p.suffix.casefold() not in _TEXT or p.name.startswith('.env') or _SECRET_FIELD.search(p.name):
                            item.update(included=False,reason='binary, archive or credential file; inventory only')
                        else:
                            raw=p.read_bytes()
                            try:text=raw.decode('utf-8-sig');item['encoding']='utf-8'
                            except UnicodeDecodeError:
                                text=raw.decode('cp1251');item['encoding']='cp1251'
                            name=label+'/'+rel
                            sanitized=False
                            if p.suffix.casefold()=='.json':
                                try:
                                    text=json.dumps(r.value(json.loads(text)),ensure_ascii=False,indent=2)
                                    sanitized=True
                                except ValueError:
                                    warnings.append({'file':name,'reason':'invalid_json_preserved_as_text'})
                            elif p.suffix.casefold()=='.jsonl':
                                lines=[]
                                for lineno,line in enumerate(text.splitlines(keepends=True),1):
                                    try:
                                        event=json.loads(line)
                                        if label=='workspace' and p.name=='application_events.jsonl' and isinstance(event,dict):
                                            kind=event.get('event')
                                            if kind in ('task_capture_started','model_call_started','model_call_completed','model_call_failed'):trace_coverage[kind]+=1
                                            if kind in ('model_call_started','model_call_completed','model_call_failed'):
                                                trace_coverage['last_model_event_at']=event.get('time')
                                        lines.append(json.dumps(r.value(event),ensure_ascii=False)+'\n')
                                    except ValueError:
                                        lines.append(r.text(line))
                                        if line.strip():warnings.append({'file':name,'line':lineno,'reason':'invalid_jsonl_line_preserved'})
                                text=''.join(lines);sanitized=True
                            write(name,text,sanitized=sanitized)
                            item.update(included=True,archive_path=name,original_sha256=hashlib.sha256(raw).hexdigest())
                            after=p.stat()
                            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                                warnings.append({'file':label+'/'+rel,'reason':'changed_during_snapshot'})
                    except Exception as exc:
                        item.update(included=False,reason=str(exc));warnings.append({'file':label+'/'+rel,'error':str(exc)})
                    inventory.append(item)
            versions={}
            for name in ('PySide6','Pillow','gradio_client'):
                try:versions[name]=importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError:versions[name]='not_installed'
            env=os.environ if environ is None else environ
            runtime={'platform':platform.platform(),'python':sys_version(),'executable':os.sys.executable,
                'dependencies':versions,'ffmpeg':shutil.which('ffmpeg'),
                'configuration':{k:env[k] for k in _ENV_FIELDS if k in env},
                'key_availability':{k:bool(v) for k,v in env.items() if k in ('OPENAI_API_KEY','GROQ_API_KEY','OPENROUTER_API_KEY')},
                'ollama':ollama_inventory()}
            try:
                from hardware_profile import detect_hardware
                runtime['hardware']=detect_hardware()
            except Exception as exc:runtime['hardware']={'status':'unavailable','error':str(exc)}
            obj('runtime/environment.json',runtime)
            try:
                import release_guard
                integrity=release_guard.verify_tree(source_root)
            except Exception as exc:integrity={'status':'UNVERIFIED','error':str(exc)}
            obj('installation/source_integrity.json',integrity)
            if integrity.get('status')!='PASS':warnings.append({'reason':'source_integrity_not_verified','details':integrity})
            # The running installation's own identity, not a claimed release.
            import candidate_identity,version_info
            obj('installation/identity.json',{'candidate_id':candidate_identity.CANDIDATE_ID,'version':version_info.APP_VERSION})
            if not any(i['included'] and i['path'].endswith('application_events.jsonl') for i in inventory):
                warnings.append({'reason':'no_application_trace','details':'Older tasks may have no model request/response capture.'})
            if trace_coverage['model_call_started']==0:
                warnings.append({'reason':'no_model_request_trace','details':'An interface/history journal is not evidence of model execution. No full model request trace is present for these saved tasks.'})
            obj('FILE_INVENTORY.json',inventory)
            write('README.txt','''ОТЧЁТ AI SMM MANAGER ДЛЯ АНАЛИЗА

Начните с SUMMARY.json. project/snapshot.json содержит только выбранный проект:
все доступные проектные таблицы, задания, pending, journal, документы и историю.
documents/*.md — полные сохранённые тексты; project/current_editor.md — текущий редактор.
workspace/drafts — черновики, checkpoints, проверки, отказы, полный application_events.jsonl.
В application_events сохранены запросы/схемы/ответы моделей и исключения с traceback
для заданий, выполненных после установки версии с записью трассы.
runtime — общие технические журналы приложения, зависимости, конфигурация и модели.
installation — фактические текстовые исходники/настройки и результат проверки целостности.
FILE_INVENTORY.json перечисляет включённые и невключённые файлы и причины.
Медиа, PDF, базы данных и архивы перечислены, но бинарные файлы не вложены.
API-ключи и другие распознанные секреты удалены из текстов. Исходные файлы не менялись.
Это снимок данных, не подтверждение успешного задания или качества контента.
При работающей задаче снимок БД и файлов сделан последовательно; изменения перечислены.
''')
            summary={'schema_version':1,'created_at':utc_now(),'project_id':snapshot['project']['id'],
                'project_name':snapshot['project'].get('name'),
                'status':'COLLECTED_WITH_WARNINGS' if warnings else 'COLLECTED',
                'scope':'selected_project_database_and_all_readable_text_plus_shared_application_logs_and_installation',
                'document_count':len(snapshot['tables'].get('manager_documents',[])),
                'message_count':len(snapshot['tables'].get('messages',[])),
                'model_trace_coverage':trace_coverage,
                'pending_tasks':snapshot['tables'].get('ai_pending_tasks',[]),
                'warnings':warnings,'redactions':r.replacements,'files':entries.copy()}
            obj('SUMMARY.json',summary)
        with zipfile.ZipFile(tempname) as z:
            if z.testzip() is not None:raise OSError('Ошибка проверки ZIP-отчёта')
            for name,item in entries.items():
                if hashlib.sha256(z.read(name)).hexdigest()!=item['sha256']:raise OSError('Нарушена целостность '+name)
        os.replace(tempname,target)
        return {'path':str(target),'files':len(written),'warnings':len(warnings),'bytes':target.stat().st_size}
    finally:
        Path(tempname).unlink(missing_ok=True)


def sys_version():return platform.python_version()
