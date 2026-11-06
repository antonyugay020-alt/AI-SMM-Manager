"""Task-scoped artifact verification, revision provenance and pending storage.

A document's kind alone does not establish its provenance. Only a matching
execution receipt and content digest permit reuse for an explicit resume.
"""
import hashlib
import json
import re
from pathlib import Path

def ensure_pending_revision_column(db):
    if 'revision_id' not in {row[1] for row in db.execute('PRAGMA table_info(ai_pending_tasks)')}:
        db.execute('ALTER TABLE ai_pending_tasks ADD COLUMN revision_id TEXT')


def store_pending_task(db,project_id,request,question,revision_id=None,*,task_id=None,force_new=False):
    """Persist human clarification and revision identity independently."""
    ensure_pending_revision_column(db)
    if revision_id is not None and (not isinstance(revision_id,str) or not re.fullmatch(r'[0-9a-f]{32}',revision_id)):
        raise ValueError('Некорректный идентификатор редакции')
    from task_lifecycle import TaskLifecycle
    return TaskLifecycle(db).activate(project_id,request,question,revision_id,task_id=task_id,force_new=force_new)


def original_document_request(db,project_id,document_id):
    """Only a matching persisted receipt can identify the original task."""
    row=db.execute('SELECT kind,content FROM manager_documents WHERE id=? AND project_id=?',
                   (document_id,project_id)).fetchone()
    if not row:return None
    digest=hashlib.sha256(row[1].encode('utf-8')).hexdigest()
    rows=db.execute('SELECT request,details FROM ai_execution_journal WHERE project_id=? '
        'AND artifact_kind=? AND artifact_sha256=? AND status IN (\'saved\',\'reused\') '
        'ORDER BY id DESC',(project_id,row[0],digest)).fetchall()
    legacy=[]
    for request,details in rows:
        try:record=json.loads(details)
        except (ValueError,TypeError):record={}
        if isinstance(record,dict) and record.get('artifact_id'):
            if record['artifact_id']==document_id:return request
        else:legacy.append(request)
    return legacy[0] if len(set(legacy))==1 else None



def revision_seed(db,project_id,request,document_id,*,document_ids=None):
    """Copy visible saved artifacts as UNAPPROVED revision input, never facts."""
    from editorial_evidence_matrix import publication_rows
    from publication_schema import POST_FIELDS
    from platform_registry import explicit_platforms,canonicalize_platforms
    if original_document_request(db,project_id,document_id)!=request:
        raise ValueError('Не подтверждено исходное задание выбранного документа')
    selected=db.execute('SELECT kind,content FROM manager_documents WHERE id=? AND project_id=?',
                        (document_id,project_id)).fetchone()
    if document_ids is not None:
        if not isinstance(document_ids,list) or len(document_ids)!=2 or len(set(document_ids))!=2:
            raise ValueError('Не выбрана единственная пара документов')
        bodies={}
        for ident in document_ids:
            if original_document_request(db,project_id,ident)!=request:
                raise ValueError('Документ не связан с исходным заданием')
            row=db.execute('SELECT kind,content FROM manager_documents WHERE id=? AND project_id=?',(ident,project_id)).fetchone()
            if not row or row[0] in bodies:raise ValueError('Неоднозначный состав комплекта')
            bodies[row[0]]=row[1]
        if set(bodies)!={'strategy','calendar'}:raise ValueError('Не выбраны стратегия и календарь')
    else:
        bodies={kind:verified_document(db,project_id,request,kind) for kind in ('strategy','calendar')}
        bodies[selected[0]]=selected[1]
    if not all(bodies.values()):raise ValueError('Не найден сохранённый комплект одного исходного задания')
    channels=[] if document_ids is not None else explicit_platforms(request)
    rows=publication_rows(bodies['calendar'])
    if not channels:
        channels=canonicalize_platforms(', '.join(row['typed_fields'].get('platform_format','').partition(',')[0]
                                                for row in rows))
    if len(channels)!=1:raise ValueError('Не определена единственная площадка исходного задания')
    posts={};slots=[]
    for row in rows:
        match=re.fullmatch(r'(\d+)/(.+)',row['slot'])
        data=row['typed_fields']
        if not match or any(not data.get(key) for key in POST_FIELDS):
            raise ValueError('Сохранённая публикация не содержит всех исходных полей')
        post={key:data[key] for key in POST_FIELDS};post['id']=row['slot'];posts[post['id']]=post
        fmt=data.get('platform_format','').partition(',')[2].strip()
        if not fmt:raise ValueError('Не найден формат сохранённой публикации')
        slots.append({'week':int(match.group(1)),'day':match.group(2),'topic':post['topic'],
            'brief':data.get('goal') or post['topic'],'goal':data.get('goal') or post['topic'],
            'audience':post['audience'],'platform':channels[0],'format':fmt})
    from content_requirements import publication_scope
    if document_ids is not None:
        from collections import Counter
        from content_requirements import PublicationScope
        counts=Counter(slot['week'] for slot in slots)
        if not counts or set(counts)!=set(range(1,max(counts)+1)) or len(set(counts.values()))!=1:
            raise ValueError('Сохранённый календарь содержит неполные недели')
        weeks=max(counts);frequency=next(iter(counts.values()))
        if not 1<=weeks<=26 or not 1<=frequency<=7:raise ValueError('Неподдерживаемый объём сохранённого календаря')
        scope=PublicationScope(weeks,frequency,len(posts),len(posts),'saved_structure')
        from publication_schedule import publication_ids
        if set(posts)!=set(publication_ids(weeks,frequency)):
            raise ValueError('Сохранённый календарь не соответствует технической структуре слотов')
    else:scope=publication_scope(request)
    if len(posts)!=scope.total:raise ValueError('Сохранённый комплект не совпадает с объёмом исходного задания')
    return {'project_id':project_id,'request':request,'strategy':bodies['strategy'],
            'posts':posts,'slots':slots,'platform':channels[0],'source_document_id':document_id,'scope':{'weeks':scope.weeks,'per_week':scope.per_week,'total':scope.total}}


def load_revision_seed(workspace,project_id,request,revision_id):
    if not isinstance(revision_id,str) or not re.fullmatch(r'[0-9a-f]{32}',revision_id):
        raise ValueError('Некорректный идентификатор редакции')
    data=json.loads((Path(workspace)/'drafts'/('revision_input_'+revision_id+'.json')).read_text(encoding='utf-8'))
    if not isinstance(data,dict) or data.get('project_id')!=project_id or data.get('request')!=request:
        raise ValueError('Редакция не принадлежит проекту и исходному заданию')
    return data


def verify_saved_artifact(db, project_id, kind, content, export):
    """Confirm current DB bytes and reread required exports before task closure.

    Export failure is recoverable without inserting another document. Exporter
    is injected so this storage contract can be checked without importing Qt.
    A PDF header alone is insufficient: require a completed PDF trailer too.
    """
    if not is_latest_document(db, project_id, kind, content):
        return False
    row = db.execute(
        'SELECT id,title,content FROM manager_documents WHERE project_id=? AND kind=? '
        'ORDER BY updated_at DESC, rowid DESC LIMIT 1', (project_id, kind)).fetchone()
    paths = export(project_id, row[0], kind, row[1], row[2])
    if not isinstance(paths, (list, tuple)) or len(paths) < 2:
        return False
    text, pdf = Path(paths[0]), Path(paths[1])
    if text.read_bytes().decode('utf-8') != content:
        return False
    data = pdf.read_bytes()
    if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-1024:]:
        return False
    return is_latest_document(db, project_id, kind, content)


def verified_document(db, project_id, request, kind):
    """Return content only if the current saved document belongs to this request."""
    if not project_id or not request or kind not in ('strategy', 'calendar'):
        return None
    try:
        # Only the current latest document can be reused. An older receipt
        # must not override a more recent manual edit or a new assignment.
        row = db.execute(
            'SELECT id,content FROM manager_documents WHERE project_id=? AND kind=? '
            'ORDER BY updated_at DESC, rowid DESC LIMIT 1',
            (project_id, kind)).fetchone()
        if not row or not isinstance(row[1],str) or not row[1]:
            return None
        if original_document_request(db,project_id,row[0])==request:
            return row[1]
    except (AttributeError, TypeError):
        return None
    except Exception as exc:
        # An unavailable or legacy journal must never authorize reuse.
        if exc.__class__.__name__ in ('OperationalError', 'ProgrammingError'):
            return None
        raise
    return None


def is_latest_document(db, project_id, kind, content):
    """Return True only if the latest saved project document is this exact text.

    Checking for *any* matching historical row can falsely approve a completed
    task after the user has manually edited or replaced the current document.
    """
    if not project_id or kind not in ('strategy', 'calendar') or not isinstance(content, str) or not content.strip():
        return False
    try:
        row = db.execute(
            'SELECT content FROM manager_documents WHERE project_id=? AND kind=? '
            'ORDER BY updated_at DESC, rowid DESC LIMIT 1', (project_id, kind)).fetchone()
    except Exception as exc:
        if exc.__class__.__name__ in ('OperationalError', 'ProgrammingError'):
            return False
        raise
    return bool(row and row[0] == content)
