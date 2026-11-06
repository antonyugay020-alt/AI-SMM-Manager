"""Task-wide durable orchestration contract, independent of document/industry type.

The language model proposes content; this module owns task identity and lifecycle.
Persist only task metadata, never model text or user private content in this ledger.
The existing per-artifact checkpoints remain authoritative for document bytes.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import hashlib
import re
from pathlib import Path
from durable_json import atomic_write_json


STATES = frozenset({'planned', 'running', 'blocked', 'accepted'})


@dataclass
class TaskLedger:
    """Recoverable project-scoped task state; one stable ID per required output."""
    path: Path | None
    signature: str
    outputs: dict = field(default_factory=dict)
    completed: bool = False

    @classmethod
    def open(cls, drafts, signature):
        if not isinstance(signature, str) or re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', signature) is None:
            raise ValueError('Invalid task ledger identity')
        # Production task signatures are 64 lowercase hex digits. Older
        # integrations also use short stable IDs; hash their disk names rather
        # than weakening path safety or breaking existing recovery contracts.
        file_key = signature if re.fullmatch(r'[0-9a-f]{64}', signature) else hashlib.sha256(
            b'ai-smm:task-ledger-id:v1\x00' + signature.encode('utf-8')).hexdigest()
        root = Path(drafts) if drafts else None
        # The full signature is the durable identity. The old 16-character
        # filename is read only for backwards-compatible migration; two tasks
        # sharing a prefix must never overwrite each other's checkpoints.
        path = root / ('task_orchestration_'+file_key+'.json') if root else None
        legacy = root / ('task_orchestration_'+signature[:16]+'.json') if root else None
        source = path if path and path.is_file() else legacy
        ledger = cls(path, signature)
        if source and source.is_file():
            try:
                record = json.loads(source.read_text(encoding='utf-8'))
                if not isinstance(record,dict) or record.get('signature') != signature or not isinstance(record.get('outputs'),dict):
                    return ledger
                fully_valid = True
                for ident, entry in record['outputs'].items():
                    if (not isinstance(ident,str) or not ident.strip() or
                            not isinstance(entry,dict) or entry.get('status') not in STATES):
                        fully_valid = False
                        continue
                    try:
                        revisions = int(entry.get('revisions',0))
                    except (ValueError,TypeError,OverflowError):
                        # A malformed metadata entry cannot invalidate later
                        # valid records or the actual document checkpoints.
                        fully_valid = False
                        continue
                    ledger.outputs[ident] = {
                        'kind':str(entry.get('kind') or 'artifact'),
                        'status':entry['status'],
                        'revisions':max(0,revisions),
                        'reason':str(entry.get('reason') or '')[:400],
                    }
                ledger.completed = (fully_valid and record.get('completed') is True and bool(ledger.outputs) and all(
                    entry['status']=='accepted' for entry in ledger.outputs.values()))
            except (OSError, ValueError, TypeError, OverflowError):
                pass
        return ledger

    def _commit(self, new_outputs, new_completed):
        """All-or-nothing state transitions, even on disk-full failures."""
        previous_outputs, previous_completed = self.outputs, self.completed
        self.outputs, self.completed = new_outputs, new_completed
        try:
            self.persist()
        except BaseException:
            self.outputs, self.completed = previous_outputs, previous_completed
            raise

    def register(self, kind, identifiers):
        new_outputs = dict(self.outputs)
        changed = False
        for ident in identifiers:
            if not isinstance(ident, str) or not ident.strip():
                raise ValueError('Invalid deliverable identity')
            key = ident
            if key in new_outputs and new_outputs[key]['kind'] != str(kind):
                raise ValueError('Conflicting deliverable kind for identity: '+key)
            if key not in new_outputs:
                changed = True
                new_outputs[key] = {'kind':str(kind), 'status':'planned',
                                    'revisions':0,'reason':''}
        if changed:
            self._commit(new_outputs, False)

    def set_status(self, ident, status, *, reason=''):
        if status not in STATES:
            raise ValueError('Invalid task state: '+str(status))
        if ident not in self.outputs:
            raise KeyError('Unregistered deliverable: '+str(ident))
        entry = dict(self.outputs[ident])
        if entry['status'] == 'accepted' and status != 'accepted':
            raise ValueError('Cannot silently reopen accepted artifact: '+str(ident))
        new_reason = str(reason)[:400]
        if entry['status'] == status and entry.get('reason','') == new_reason:
            return
        entry['revisions'] += 1
        entry['status'] = status
        entry['reason'] = new_reason
        new_outputs = dict(self.outputs)
        new_outputs[ident] = entry
        # Annotation of an already accepted result cannot reopen a completed task.
        self._commit(new_outputs, self.completed and all(
            value['status'] == 'accepted' for value in new_outputs.values()))

    def reconcile(self, accepted_ids, *, strategy=False, calendar=False):
        """Checkpoint bytes win over metadata; never invent artifact acceptance."""
        accepted = {str(x) for x in accepted_ids}
        if strategy: accepted.add('strategy')
        if calendar: accepted.add('calendar')
        changed = False
        new_outputs = {}
        for ident, old_entry in self.outputs.items():
            entry = dict(old_entry)
            if ident in accepted and entry['status'] != 'accepted':
                entry['status'] = 'accepted'
                entry['reason'] = ''
                changed = True
            elif ident not in accepted and entry['status'] == 'accepted':
                entry['status'] = 'planned'
                entry['reason'] = 'Checkpoint missing; must be revalidated'
                changed = True
            new_outputs[ident] = entry
        new_completed = bool(new_outputs) and all(e['status'] == 'accepted' for e in new_outputs.values())
        if changed or new_completed != self.completed:
            self._commit(new_outputs,new_completed)

    def summary(self):
        counts = {status:sum(e['status']==status for e in self.outputs.values()) for status in sorted(STATES)}
        return {'total':len(self.outputs), 'counts':counts, 'completed':self.completed,
                'blocked':{ident:e['reason'] for ident,e in self.outputs.items() if e['status']=='blocked'}}

    def persist(self):
        if not self.path: return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload={'schema_version':1,'signature':self.signature,'outputs':self.outputs,
                 'completed':self.completed,'updated_at':datetime.now(timezone.utc).isoformat()}
        atomic_write_json(self.path, payload)
