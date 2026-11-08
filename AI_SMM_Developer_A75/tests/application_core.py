"""Qt-free application boundary for durable project effects and task completion.

A single coordinator owns transaction acquisition, projection replay, and
verified completion. UI handlers never own transaction lifetimes for these
operations. Every API receives an explicit project identity.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
import sqlite3
from project_file_boundary import require_managed_folder, require_managed_file
from project_text_projection import register, drain as drain_text
from file_effect_outbox import drain as drain_documents
from completion_ledger import finalize_document
from project_recovery_service import recover
from sqlite_transaction_core import own

class DocumentIdentityCollision(ValueError, RuntimeError):
    """Invalid export identity and a runtime ownership collision."""


@dataclass(frozen=True)
class TextResult:
    project_id: str
    file_id: str
    path: Path
    committed: bool
    exported: bool

def save_project_text(db, workspace, project_id: str, content: str, timestamp: str, *, file_id=None) -> TextResult:
    if db.in_transaction:
        raise RuntimeError('Application transaction owned by another operation')
    if not isinstance(project_id,str) or not project_id:
        raise ValueError('Missing project identity')
    ident=file_id or uuid4().hex
    base=Path(workspace(project_id))
    folder=require_managed_folder(base,'texts')
    dest=require_managed_file(base,folder/(ident+'.txt'),('texts',))
    with own(db):
        if not db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():
            raise ValueError('Project does not exist')
        register(db,file_id=ident,project_id=project_id,filename=dest.name,
                 content=content,full_path=dest,timestamp=timestamp)

    report=drain_text(db,workspace,project_id=project_id)
    exported=(not report['failed'] and db.execute(
        'SELECT state FROM project_text_projections WHERE project_id=? AND file_id=?',
        (project_id,ident)).fetchone()==('done',))
    return TextResult(project_id,ident,dest,True,exported)

def complete_ai_document(db, workspace, exporter, notice_factory, *,
                         project_id, artifact_id, request, kind, title, task_id):
    if db.in_transaction:
        raise RuntimeError('Application transaction owned by another operation')
    drain_documents(db,workspace,project_id=project_id,export_document=exporter)
    incomplete=db.execute('SELECT count(*) FROM ai_file_effect_outbox WHERE project_id=? AND artifact_id=? AND state<>?',
                          (project_id,artifact_id,'done')).fetchone()[0]
    if incomplete: return False
    return finalize_document(db,project_id=project_id,artifact_id=artifact_id,
                             request=request,task_id=task_id,notice=notice_factory(kind,title))

def restore_project_results(db,workspace,exporter,notice_factory,*,limit=1000):
    if db.in_transaction:
        raise RuntimeError('Application transaction owned by another operation')
    return recover(db,workspace,exporter,notice_factory,limit=limit)


def save_edited_document(db, workspace, exporter, *, project_id, document_id,
                         kind, title, content, updated_at, diagram_png=None):
    """Shared transaction/replay boundary for edits made by the Qt document editor."""
    from manual_document_projection import save
    return save(db,workspace,exporter,project_id=project_id,document_id=document_id,
                kind=kind,title=title,content=content,updated_at=updated_at,diagram_png=diagram_png)


def restore_edited_document_exports(db,workspace,exporter,*,project_id=None,limit=100):
    from manual_document_projection import drain
    return drain(db,workspace,exporter,project_id=project_id,limit=limit)


@dataclass(frozen=True)
class MediaBatchResult:
    """A project-scoped application outcome, independent of Qt widgets."""
    project_id: str
    receipts: tuple
    failures: tuple


def import_project_media(db, workspace, sources, *, project_id, kind, allowed_extensions=None):
    """Single application boundary for assets and references.

    A failed source cannot cancel successful imports of the same batch;
    all receipts are scoped to one immutable project identity. Durable media
    staging, ledger writes and publication are delegated to media_ingest_core.
    """
    from media_ingest_core import ingest
    if db.in_transaction:
        raise RuntimeError('Cannot import media inside an unrelated transaction')
    if not isinstance(project_id, str) or not project_id:
        raise ValueError('Missing project identity')
    if kind not in ('asset', 'reference'):
        raise ValueError('Invalid media kind')
    try:
        found = db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone()
    except sqlite3.Error as exc:
        return MediaBatchResult(project_id, (), tuple((str(source), str(exc)) for source in tuple(sources)))
    if not found:
        raise ValueError('Project does not exist')
    allowed = None if allowed_extensions is None else frozenset(allowed_extensions)
    receipts, failures = [], []
    for source in tuple(sources):
        src = Path(source)
        if allowed is not None and src.suffix.lower() not in allowed:
            failures.append((str(source), 'Unsupported media extension'))
            continue
        try:
            receipt = ingest(db, workspace, src, project_id=project_id, kind=kind)
            receipts.append(receipt)
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
            failures.append((str(source), str(exc)))
    return MediaBatchResult(project_id, tuple(receipts), tuple(failures))


def recover_media_effects(db, workspace, *, project_id=None, limit=100):
    """Qt-independent media replay; the UI only displays the receipt."""
    from media_ingest_core import replay
    return replay(db, workspace, project_id=project_id, limit=limit)


def _validate_document_reservation(db, workspace, project_id, document_id, kind, title):
    """Canonical filename ownership for all document creation paths.

    Includes current document exports and old filenames still reserved by an
    unfinished rename. No filesystem mutation and no independent commit.
    """
    from manual_document_projection import _paths, FOLDERS
    import json
    if not db.in_transaction:
        raise RuntimeError('Document reservation requires an owned transaction')
    if not db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():
        raise ValueError('Unknown destination project')
    # Export filenames contain only the first eight identity characters.
    # Reserve that prefix project-wide even if titles currently differ: a later
    # rename must never make two durable documents map to one export identity.
    collision=db.execute('SELECT id FROM manager_documents WHERE project_id=? AND substr(id,1,8)=? AND id<>? LIMIT 1',
                         (project_id,document_id[:8],document_id)).fetchone()
    if collision:
        raise DocumentIdentityCollision('Коллизия идентификаторов документов при экспорте')
    root=Path(workspace(project_id))
    targets=set(_paths(root,document_id,kind,title))
    if any(path.exists() or path.is_symlink() for path in targets):
        raise FileExistsError('A new document cannot overwrite an existing export')
    for other_id, other_kind, other_title in db.execute(
            'SELECT id,kind,title FROM manager_documents WHERE project_id=?',(project_id,)):
        if other_kind in FOLDERS and targets.intersection(_paths(root,other_id,other_kind,other_title)):
            raise ValueError('Another document owns this export name')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manager_document_exports'").fetchone():
        for other_id, orphan_json in db.execute(
                'SELECT document_id,orphan_json FROM manager_document_exports WHERE project_id=?',
                (project_id,)):
            orphans=json.loads(orphan_json)
            if not isinstance(orphans,list):raise ValueError('Corrupt export reservations')
            for pair in orphans:
                if (not isinstance(pair,list) or len(pair)!=2 or pair[0] not in FOLDERS
                        or not isinstance(pair[1],str)):
                    raise ValueError('Corrupt export reservation identity')
                if targets.intersection(_paths(root,other_id,pair[0],pair[1])):
                    raise ValueError('Export name reserved by a pending rename')
    return root


def create_project_document(db, workspace, exporter, *, project_id, document_id,
                            kind, title, content, created_at):
    """Accept a new document atomically, then project verified PDF/TXT.

    No provisional filesystem export precedes the SQLite commit. If rendering
    fails, the document and durable export intent remain available for replay.
    """
    from manual_document_projection import register,drain,_paths,FOLDERS,SaveReceipt
    if db.in_transaction:
        raise RuntimeError('A different transaction owns the SQLite connection')
    if kind not in FOLDERS or not isinstance(content,str) or not isinstance(title,str) or not title.strip():
        raise ValueError('Invalid document kind or content')
    if not isinstance(project_id,str) or not project_id or not isinstance(document_id,str) or not document_id:
        raise ValueError('Missing project/document identity')
    with own(db):
        root=_validate_document_reservation(db,workspace,project_id,document_id,kind,title)
        db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?,?,?)',
            (document_id,project_id,kind,title,content,created_at,created_at))
        revision=register(db,root,project_id=project_id,document_id=document_id,
            kind=kind,title=title,content=content,updated_at=created_at)

    result=drain(db,workspace,exporter,project_id=project_id)
    state=db.execute('SELECT state,revision_sha256 FROM manager_document_exports WHERE document_id=? AND project_id=?',
                     (document_id,project_id)).fetchone()
    return SaveReceipt(project_id,document_id,revision,True,
                       bool(state and state==('done',revision)))


def stage_generated_document_in_transaction(db, workspace, *, project_id, document_id,
                                            kind, title, content, created_at):
    """Register an AI-generated document inside its *existing* completion UoW.

    The UI does not own SQL statements or export-name reservations. Completion
    and its outbox are committed later by the same outer transaction owner.
    This function MUST NOT commit, create provisional files or drain exports.
    """
    from manual_document_projection import _paths, FOLDERS
    if not db.in_transaction:
        raise RuntimeError('Generated document requires the completion transaction')
    if (not isinstance(project_id, str) or not project_id or
            not isinstance(document_id, str) or not document_id or
            kind not in FOLDERS or not isinstance(title, str) or not title.strip() or
            not isinstance(content, str) or not isinstance(created_at, str)):
        raise ValueError('Invalid generated document identity or content')
    _validate_document_reservation(db,workspace,project_id,document_id,kind,title)
    db.execute('INSERT INTO manager_documents VALUES (?,?,?,?,?,?,?)',
               (document_id, project_id, kind, title, content, created_at, created_at))
    return document_id


class ApplicationRuntime:
    """Qt-free application service. One SQLite identity and workspace resolver.

    UI adapters must not construct independent coordinators, own commits, or
    choose a project at callback time. The explicit project identity is the
    authority for every operation exposed here.
    """
    def __init__(self, db: sqlite3.Connection, workspace):
        if not isinstance(db, sqlite3.Connection) or not callable(workspace):
            raise TypeError('Application runtime requires SQLite and a workspace resolver')
        self.db, self.workspace = db, workspace
        from project_runtime import ProjectRuntime
        from execution_runtime import ExecutionRuntime
        from result_publication_runtime import ResultPublicationRuntime
        from conversation_runtime import ConversationRuntime
        from task_outcome_runtime import TaskOutcomeRuntime
        from project_data_runtime import ProjectDataRuntime
        self.data = ProjectDataRuntime(db)
        self.outcomes = TaskOutcomeRuntime(db)
        from publication_runtime import PublicationRuntime
        from batch_runtime import BatchRuntime
        self.publication_queue = PublicationRuntime(db, workspace)
        self.batches = BatchRuntime(db, workspace)
        from project_result_retirement import ProjectResultRetirement
        self.result_retirements = ProjectResultRetirement(db, workspace)
        self.conversations = ConversationRuntime(db)
        self.projects = ProjectRuntime(db)
        self.executions = ExecutionRuntime(db)
        self.publications = ResultPublicationRuntime(db, workspace, execution=self.executions, batches=self.batches)

    def close(self):
        """Dispose the application's database after its worker fence has drained.

        Pending outbox effects remain durable for restart. Closing must never
        silently discard a transaction belonging to a different operation.
        The Qt adapter is responsible for checking native worker completion.
        """
        if getattr(self, '_closed', False):
            return
        if self.db.in_transaction:
            raise RuntimeError('Cannot close while an operation owns the transaction')
        self.db.close()
        self._closed = True

    def execution_database_path(self):
        path=next((row[2] for row in self.db.execute('PRAGMA database_list') if row[1]=='main'),'')
        if not path:raise RuntimeError('Durable execution database required')
        return path

    def cancel_text_execution(self,project_id,request,*,task_id):
        """Own the registry/fencing/journal commit; UI never commits SQL."""
        with own(self.db):
            return self.executions.cancel(project_id,request,task_id=task_id)

    def record_execution_failure(self, *, project_id, attempt=None, response=None, timestamp=None):
        """One failure commit: settle worker authority and persist its explanation.

        The captured originating project, not the current Qt selection, owns
        both effects. A crashed write leaves both unchanged for recovery.
        """
        from execution_runtime import ExecutionReceipt
        if self.db.in_transaction:
            raise RuntimeError('Cannot settle worker inside another transaction')
        if not isinstance(project_id,str) or not project_id:
            raise ValueError('Missing original project identity')
        if attempt is not None and (getattr(attempt,'project_id',None)!=project_id
                                     or not getattr(attempt,'task_id',None)):
            raise ValueError('Failure belongs to another project or task')
        if response is not None and (not isinstance(response,str) or not response.strip()):
            raise ValueError('Missing failure explanation')
        with own(self.db):
            if not self.db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():
                raise ValueError('Unknown task origin')
            if attempt is not None:
                self.executions.fail(ExecutionReceipt(project_id,attempt.task_id,attempt))
            if response is not None:
                return self.outcomes.record_answer_in_transaction(project_id,response,timestamp=timestamp)
        return None

    def start_dialogue_execution(self, plan, request, context):
        """Atomically bind the instruction, task and execution attempt.

        The worker cannot start until the complete identity graph has committed.
        A failure to allocate its attempt rolls back the task activation,
        dialogue binding, context and questions as one operation.
        """
        from dialogue_execution import persist_execution
        from sqlite_transaction_core import own
        if self.db.in_transaction:
            raise RuntimeError('Another application operation owns the transaction')
        if getattr(plan, 'project_id', None) is None or not isinstance(request, str):
            raise ValueError('Missing immutable execution identity')
        with own(self.db):
            claim=persist_execution(self.db, plan, request, context)
            task_id=context.get('task_id')
            if not isinstance(task_id, str) or not task_id:
                raise ValueError('Task registration did not issue a stable identity')
            attempt=self.executions.begin(plan.project_id,task_id)
            return claim, attempt

    def recover_inflight(self, *, project_id=None):
        """Single restart fence for all worker authorities owned by this app.

        Incomplete instructions, durable batch queues, and already published
        outputs are retained. Old execution and batch callbacks are rejected
        together, or neither authority changes if SQLite recovery fails.
        """
        if self.db.in_transaction:
            raise RuntimeError('Cannot recover workers inside another transaction')
        from sqlite_transaction_core import own
        with own(self.db):
            interrupted=self.executions.recover(project_id=project_id)
            batches=self.batches.recover_in_transaction(project_id=project_id)
        return {'execution_attempts':interrupted,'batch_items':batches}

    def prepare_project_switch(self, project_id):
        """One project-bound preflight before any UI selection is changed.

        A workspace may be projected and pending retirements replayed, but a
        failed preflight never changes the active UI project or its task state.
        The returned snapshot is always scoped to the requested project.
        """
        if self.db.in_transaction:
            raise RuntimeError('Cannot switch projects during another transaction')
        if not isinstance(project_id, str) or not project_id:
            raise ValueError('Missing destination project')
        self.projects.activate(project_id)
        retirements = self.result_retirements.recover(project_id=project_id)
        if retirements.failed:
            raise RuntimeError('Destination project has unresolved result retirements')
        view = self.data.workspace(project_id)
        if view.project_id != project_id:
            raise RuntimeError('Project read model returned a foreign project')
        return view

    def configure_project_control(self, backup_root):
        from project_control_runtime import ProjectControlRuntime
        self.controls=ProjectControlRuntime(self.db,self.workspace,backup_root)
        return self.controls

    def stage_generated_document(self, *, project_id, document_id, kind, title, content, created_at):
        return stage_generated_document_in_transaction(self.db, self.workspace,
            project_id=project_id, document_id=document_id, kind=kind,
            title=title, content=content, created_at=created_at)

    def create_document(self, exporter, *, project_id, document_id, kind, title, content, created_at):
        return create_project_document(self.db,self.workspace,exporter,project_id=project_id,
            document_id=document_id,kind=kind,title=title,content=content,created_at=created_at)

    def save_text(self, project_id, content, timestamp):
        return save_project_text(self.db, self.workspace, project_id, content, timestamp)

    def save_document(self, exporter, *, project_id, document_id, kind, title,
                      content, updated_at, diagram_png=None):
        return save_edited_document(self.db, self.workspace, exporter,
            project_id=project_id, document_id=document_id, kind=kind, title=title,
            content=content, updated_at=updated_at, diagram_png=diagram_png)

    def complete_document(self, exporter, notice_factory, *, project_id,
                          artifact_id, request, kind, title, task_id):
        return complete_ai_document(self.db, self.workspace, exporter, notice_factory,
            project_id=project_id, artifact_id=artifact_id, request=request,
            kind=kind, title=title, task_id=task_id)

    def restore(self, exporter, notice_factory, *, limit=1000):
        return restore_project_results(self.db, self.workspace, exporter, notice_factory, limit=limit)

    def restore_document_edits(self, exporter, *, project_id=None, limit=100):
        return restore_edited_document_exports(self.db, self.workspace, exporter,
                                               project_id=project_id, limit=limit)

    def import_result_file(self, project_id, source, *, item_id=None):
        """Recoverable manual result import through the same media spool/outbox.

        The selected project identity is immutable and independent of any later
        UI selection. A committed intent remains replayable after a disk crash.
        """
        from media_ingest_core import ingest
        return ingest(self.db,self.workspace,source,project_id=project_id,
                      kind='result',item_id=item_id)

    def record_local_command(self, project_id, content, *, timestamp=None):
        """Durable local user instruction; the UI must not commit SQLite."""
        return self.publications.registry.publish_message(
            project_id, content, role='user', timestamp=timestamp)

    def import_media(self, sources, *, project_id, kind, allowed_extensions=None):
        return import_project_media(self.db, self.workspace, sources,
            project_id=project_id, kind=kind, allowed_extensions=allowed_extensions)

    def restore_media(self, *, project_id=None, limit=100):
        return recover_media_effects(self.db, self.workspace, project_id=project_id, limit=limit)

    def publish_result(self, project_id, kind, value, *, receipt=None,
                       active_project_id=None, operation_id=None, completion_request=None, dialogue_turn_id=None, batch_claim=None):
        return self.publications.publish(project_id, kind, value,
            receipt=receipt, active_project_id=active_project_id,
            operation_id=operation_id, completion_request=completion_request, dialogue_turn_id=dialogue_turn_id, batch_claim=batch_claim)
