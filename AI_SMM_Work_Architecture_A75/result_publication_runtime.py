"""Single transactional admission boundary for asynchronous project results.

All result kinds use the same durable authority check and project boundary. The
worker attempt remains running until the task's completion gate accepts it;
publication never silently completes an unfinished task.
"""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import sqlite3
from execution_runtime import ExecutionRuntime, ExecutionReceipt
from sqlite_transaction_core import own
from project_result_registry import ProjectResultRegistry
from file_evidence import capture, unchanged

def _media_digest(value):
    from pathlib import Path
    digest = sha256()
    with Path(value).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class PublishedResult:
    project_id: str
    kind: str
    record_id: object

class ResultPublicationRuntime:
    def __init__(self, db: sqlite3.Connection, workspace=None, *, execution=None, batches=None):
        if execution is not None and (not isinstance(execution, ExecutionRuntime) or execution.db is not db):
            raise ValueError('Publication and execution must share one application database')
        self.db = db
        self.registry = ProjectResultRegistry(db, workspace)
        self.execution = execution if execution is not None else ExecutionRuntime(db)
        if batches is not None and batches.db is not db:
            raise ValueError("Batch publication must share application database")
        self.batches = batches

    def publish(self, project_id, kind, value, *, receipt=None, active_project_id=None, operation_id=None, completion_request=None, dialogue_turn_id=None, batch_claim=None):
        """Fenced atomic admission. No cross-project writes or foreign commits."""
        if self.db.in_transaction:
            raise RuntimeError('Another operation owns the SQLite transaction')
        if dialogue_turn_id is not None and (not isinstance(dialogue_turn_id,str) or not dialogue_turn_id.strip() or operation_id is None):
            raise ValueError('Dialogue completion requires turn identity and durable operation identity')
        if completion_request is not None and receipt is None:
            raise ValueError('Completion requires a fenced worker receipt')
        if isinstance(receipt, ExecutionReceipt) and operation_id is None:
            operation_id = f'worker:{receipt.attempt.attempt_id}:{kind}'
        if batch_claim is not None:
            if operation_id is not None or dialogue_turn_id is not None or receipt is not None:
                raise ValueError('Batch worker and dialogue/attempt authority cannot be mixed')
            operation_id = f'batch:{batch_claim.batch_id}:{batch_claim.token}:{kind}'
        if receipt is not None:
            if not isinstance(receipt, ExecutionReceipt):
                raise ValueError('Invalid execution receipt')
            if active_project_id is None:
                raise ValueError('Active project identity is required')
            if project_id != receipt.project_id:
                raise ValueError('Result project differs from worker project')
            # Ownership is checked under BEGIN IMMEDIATE, after replay identity lookup.
        elif active_project_id is not None and active_project_id != project_id:
            raise ValueError('Unowned result cannot publish into a different project')
        prepared=self.registry.prepare_media(project_id,value,kind=kind) if kind in ('video','image') else None
        payload_hash=prepared.evidence.digest if prepared is not None else sha256(str(value).encode('utf-8')).hexdigest()
        if operation_id is not None and (not isinstance(operation_id, str) or not operation_id.strip()):
            raise ValueError('Invalid publication operation identity')
        if batch_claim is not None and (kind != "image" or self.batches is None or batch_claim.project_id != project_id):
            raise ValueError("Batch image result needs matching batch ownership")
        source_evidence=capture(batch_claim.source_path,allow_empty=True) if batch_claim is not None else None
        with own(self.db):
            if batch_claim is not None:
                self.batches.authorize_in_transaction(batch_claim,source_evidence=source_evidence)
            self.db.execute('''CREATE TABLE IF NOT EXISTS result_publication_events (
                operation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                kind TEXT NOT NULL, record_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
                attempt_id TEXT, dialogue_turn_id TEXT, FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE)''')
            if 'dialogue_turn_id' not in {r[1] for r in self.db.execute('PRAGMA table_info(result_publication_events)')}:
                self.db.execute('ALTER TABLE result_publication_events ADD COLUMN dialogue_turn_id TEXT')
            if operation_id is not None:
                previous = self.db.execute('''SELECT project_id,kind,record_id,attempt_id,payload_hash,dialogue_turn_id
                    FROM result_publication_events WHERE operation_id=?''',(operation_id,)).fetchone()
                if previous:
                    if batch_claim is not None:
                        raise ValueError('Existing publication cannot acknowledge a different batch attempt')
                    expected_attempt = receipt.attempt.attempt_id if receipt is not None else None
                    if previous[0] != project_id or previous[1] != kind or previous[3] != expected_attempt or previous[4] != payload_hash or previous[5] != dialogue_turn_id:
                        raise ValueError('Publication identity collision')
                    if dialogue_turn_id is not None:
                        intake=self.db.execute('''SELECT state FROM ai_task_intake
                            WHERE project_id=? AND dialogue_turn_id=?''',(project_id,dialogue_turn_id)).fetchone()
                        if intake != ('resolved',):
                            raise ValueError('Publication replay has no committed dialogue completion')
                    if completion_request is not None:
                        # A previously committed publication must have completed its
                        # task in the same transaction. Never finalize a replay.
                        state=self.db.execute('''SELECT status FROM ai_task_registry WHERE task_id=? AND project_id=?''',
                            (receipt.task_id,project_id)).fetchone()
                        attempt_state=self.db.execute('''SELECT state FROM execution_attempts
                            WHERE attempt_id=? AND task_id=? AND project_id=?''',
                            (receipt.attempt.attempt_id,receipt.task_id,project_id)).fetchone()
                        if state != ('completed',) or attempt_state != ('accepted',):
                            raise ValueError('Publication replay has no matching completed task')
                    return PublishedResult(project_id,kind,int(previous[2]) if kind == 'message' else previous[2])
            if receipt is not None:
                # Repeat inside the transaction to fence races with cancellation.
                self.execution.authorize_result(receipt, active_project_id=active_project_id)
            if kind == 'message':
                record = self.registry.admit_message(project_id, value)
            elif kind in ('video','image'):
                record=self.registry.admit_prepared_media(project_id,prepared,require_new=batch_claim is not None)
            else:
                raise ValueError('Unsupported result kind')
            if prepared is not None and not unchanged(prepared.evidence):
                raise ValueError('Media changed during publication')
            if operation_id is not None:
                self.db.execute('''INSERT INTO result_publication_events
                    (operation_id,project_id,kind,record_id,attempt_id,payload_hash,dialogue_turn_id) VALUES(?,?,?,?,?,?,?)''',
                    (operation_id,project_id,kind,str(record),
                     receipt.attempt.attempt_id if receipt is not None else None,payload_hash,dialogue_turn_id))
            if completion_request is not None:
                self.execution.finish(receipt, completion_request)
            if batch_claim is not None:
                self.batches.accept_in_transaction(batch_claim,value,expected_sha256=payload_hash,
                    source_evidence=source_evidence,result_evidence=prepared.evidence)
            if dialogue_turn_id is not None:
                from conversation_runtime import ConversationRuntime
                ConversationRuntime(self.db).finish_in_transaction(project_id,dialogue_turn_id)
            return PublishedResult(project_id, kind, record)

