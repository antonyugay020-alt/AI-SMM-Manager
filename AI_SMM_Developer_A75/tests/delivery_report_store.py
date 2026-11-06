"""One durable authority for draft and final delivery reports.

A full task digest names a report, not a short prefix shared by distinct tasks.
A prior report is archived before replacement. Existing short-name files are
read only when their stored task signature proves exact identity.
"""
import json
import re
import uuid
from pathlib import Path
from durable_json import atomic_write_json
from project_file_boundary import reject_path_link, require_managed_folder

_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _read_report(path, signature):
    reject_path_link(path)
    report = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(report, dict) or report.get('task_signature') != signature:
        raise ValueError('Stored delivery report belongs to another task')
    return report


def store_delivery_report(workspace, signature, report, *, request=None, archive=True, stage="final"):
    """Write a checkpoint without altering a foreign task or linked path."""
    if stage not in ('final', 'draft'):
        raise ValueError('Invalid report stage')
    if not isinstance(signature, str) or _DIGEST.fullmatch(signature) is None:
        raise ValueError('Invalid task report identity')
    if not isinstance(report, dict):
        raise ValueError('Invalid delivery report')
    if report.get('task_signature') not in (None, signature):
        raise ValueError('Delivery report identity disagrees with task')
    if request is not None:
        from task_provenance import TaskProvenance
        identity = report.get('task_provenance')
        if identity is None:
            raise ValueError('Final report is missing request provenance')
        TaskProvenance.restore(identity).verify(request)
    root = Path(workspace).absolute()
    drafts = require_managed_folder(root, 'drafts')
    current = drafts / ('content_delivery_' + ('draft_' if stage == 'draft' else '') + signature + '.json')
    legacy = drafts / ('content_delivery_' + signature[:16] + '.json')
    reject_path_link(current)
    reject_path_link(legacy)
    old = None
    if current.exists():
        old = _read_report(current, signature)
    elif stage == "final" and legacy.exists():
        # A short legacy name is not an authority for a different full digest.
        # Leave it intact while writing the new, collision-free full name.
        reject_path_link(legacy)
        candidate = json.loads(legacy.read_text(encoding='utf-8'))
        if not isinstance(candidate, dict):
            raise ValueError('Invalid legacy delivery report')
        if candidate.get('task_signature') == signature:
            old = candidate
    if old is not None and request is not None:
        from task_provenance import TaskProvenance
        identity = old.get('task_provenance')
        if identity is not None:
            TaskProvenance.restore(identity).verify(request)
    payload = dict(report, task_signature=signature)
    if old is not None and archive and stage == "final":
        history_dir = require_managed_folder(root, 'drafts/content_delivery_history/' + signature)
        history_dir.mkdir(parents=True, exist_ok=True)
        history = history_dir / (uuid.uuid4().hex + '.json')
        reject_path_link(history)
        atomic_write_json(history, old)
    atomic_write_json(current, payload)
    return current
