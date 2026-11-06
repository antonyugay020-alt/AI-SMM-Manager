"""Task-owned durable filenames; never identify independent jobs by prompt text.

Legacy no-ID tasks retain their old filename, but task-bearing records never
fall back to another task's request-keyed checkpoint.
"""
from __future__ import annotations
import hashlib
import re
from pathlib import Path


def checkpoint_path(workspace, kind, request, task_id=None):
    if kind not in ('pending_text', 'text_receipt', 'task_lifecycle'):
        raise ValueError('Unknown checkpoint type')
    if not isinstance(request, str) or not request:
        raise ValueError('Missing original request')
    if task_id is not None:
        if not isinstance(task_id, str) or not re.fullmatch(r'[0-9a-f]{32}', task_id):
            raise ValueError('Invalid task identity')
        filename = kind + '_task_' + task_id + '.json'
    elif kind in ('pending_text', 'task_lifecycle'):
        filename = kind + '_' + hashlib.sha256(request.encode('utf-8')).hexdigest()[:16] + '.json'
    else:
        raise ValueError('Text receipts require a task identity')
    return Path(workspace) / 'drafts' / filename
