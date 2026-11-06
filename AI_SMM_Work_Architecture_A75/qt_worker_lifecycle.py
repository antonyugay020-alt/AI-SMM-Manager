"""Track Qt worker ownership until native completion, including failed QA paths.

A done/failed signal may run before QThread.finished. Workers must remain
referenced during that interval, including on window-close and test failures.
"""
import time

WORKER_ATTRIBUTES = (
    'task_worker', 'dialogue_worker', 'report_worker', 'video_worker',
    'cloud_worker', 'modern_worker', 'edit_worker', 'smart_mask_worker',
    'neural_worker', 'generator', 'worker', 'cuda_install_worker',
    'download_worker', 'smart_worker', 'vision_worker', 'neural_install', 'opencv_install',
)



def retain_replaced_worker(app, field, replacement):
    """Keep a running QThread alive if a result callback clears its slot.

    The native finished signal (not a result/done signal) releases ownership.
    """
    if field not in WORKER_ATTRIBUTES:
        return
    try:
        old = getattr(app, field, None)
    except (AttributeError, RuntimeError):
        return
    if old is None or old is replacement:
        return
    try:
        running = old.isRunning()
    except (AttributeError, RuntimeError):
        return
    if not running:
        return
    workers = getattr(app, '_background_workers', None)
    if workers is None:
        workers = []
        object.__setattr__(app, '_background_workers', workers)
    if any(w is old for w in workers):
        return
    workers.append(old)
    # A closure holding the worker also keeps the Python QThread wrapper alive.
    # Do not remove it on done/failed: Qt may still be unwinding at that point.
    def release():
        try:
            workers.remove(old)
        except ValueError:
            pass
    try:
        old.finished.connect(release)
    except (AttributeError, RuntimeError):
        # Fail closed: retain ownership until window destruction if unavailable.
        pass

def active_workers(app):
    candidates = [getattr(app, name, None) for name in WORKER_ATTRIBUTES]
    candidates.extend(getattr(app, '_background_workers', ()) or ())
    result = []
    for worker in candidates:
        if worker is None or any(worker is existing for existing in result):
            continue
        try:
            if worker.isRunning():
                result.append(worker)
        except RuntimeError:
            continue  # Qt wrapper already deleted
    return result


def drain_finished_workers(qapp, app, timeout_seconds=10):
    deadline = time.monotonic() + max(0, timeout_seconds)
    while True:
        qapp.processEvents()
        if not active_workers(app):
            qapp.processEvents()  # deliver queued finished/deleteLater signals
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(.025)
