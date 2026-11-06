"""One fail-closed project switch precondition for every asynchronous operation.

Blocking is intentionally limited to active operations: persisted paused tasks
must never block selection/creation of unrelated projects.
"""

WORKER_FIELDS = (
    'task_worker', 'dialogue_worker', 'worker', 'video_worker', 'generator', 'cloud_worker',
    'modern_worker', 'edit_worker', 'download_worker', 'report_worker',
)

# Qt workers retained after completion must only block while still running.
# Segmentation and installation callbacks may remain referenced for the lifetime
# of the window, so testing slot != None here would lock the GUI permanently.
RUNNING_ONLY_FIELDS = (
    'neural_worker', 'smart_worker', 'neural_install', 'cuda_install_worker',
    'vision_worker', 'smart_mask_worker', 'opencv_install',
)

def mask_owner_matches(owner, project_id, source_path):
    """Reject a mask if the selected project or photograph changed."""
    if not owner or not project_id or not source_path:
        return False
    try:
        from pathlib import Path
        return owner == (project_id, str(Path(source_path).resolve(strict=False)))
    except (OSError, ValueError, TypeError):
        return False



def project_operation_busy(view):
    if bool(getattr(view, '_dialogue_applying', False)):
        return True
    if bool(getattr(view, '_result_effects', None) is not None):
        return True
    if any(getattr(view, field, None) is not None for field in WORKER_FIELDS):
        return True
    for field in RUNNING_ONLY_FIELDS:
        worker = getattr(view, field, None)
        if worker is None:
            continue
        try:
            if worker.isRunning():
                return True
        except (AttributeError, RuntimeError):
            return True  # Detached/deleted Qt worker: fail closed.
    # Some workers are removed from their primary slots before the Qt finished
    # event. The worker list is authoritative until Qt's cleanup runs.
    for worker in getattr(view, '_background_workers', ()):
        if worker is None:
            continue
        try:
            if worker.isRunning():
                return True
        except (AttributeError, RuntimeError):
            return True
    return False


from contextlib import contextmanager

@contextmanager
def owned_sqlite_transaction(connection):
    """One application operation owns its SQL commit/rollback, never a neighbour's.

    Reject pre-existing transactions before any writes. Roll back after ANY
    exception (including filesystem and callback failures) only while this
    operation owns the transaction. The caller decides how to surface failure.
    """
    if connection.in_transaction:
        raise RuntimeError('SQLite занята незавершённой операцией; изменения не применены')
    try:
        yield connection
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
