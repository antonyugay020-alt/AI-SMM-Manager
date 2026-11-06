import unittest
from types import SimpleNamespace
from qt_worker_lifecycle import active_workers, drain_finished_workers, retain_replaced_worker

class FakeWorker:
    def __init__(self, running=True): self.running=running
    def isRunning(self): return self.running

class WorkerLifecycleTests(unittest.TestCase):
    def test_secondary_workers_block_close(self):
        for name in ('generator','neural_worker','cuda_install_worker','download_worker','smart_worker','neural_install'):
            with self.subTest(name=name):
                obj=SimpleNamespace(**{name:FakeWorker()})
                self.assertEqual(len(active_workers(obj)),1)
    def test_neural_installer_retained_until_finished(self):
        class Signal:
            def connect(self, cb): self.cb=cb
            def emit(self): self.cb()
        class Thread(FakeWorker):
            def __init__(self):
                super().__init__();self.finished=Signal()
        worker=Thread();view=SimpleNamespace(neural_install=worker,_background_workers=[])
        retain_replaced_worker(view,'neural_install',None)
        view.neural_install=None
        self.assertEqual(active_workers(view),[worker])
        worker.running=False;worker.finished.emit()
        self.assertFalse(active_workers(view))
        self.assertEqual(view._background_workers,[])

    def test_background_and_named_worker_deduplicated(self):
        w=FakeWorker()
        obj=SimpleNamespace(task_worker=w,_background_workers=[w])
        self.assertEqual(active_workers(obj),[w])
    def test_drain_does_not_claim_success_for_running_worker(self):
        class App:
            def processEvents(self): pass
        self.assertFalse(drain_finished_workers(App(),SimpleNamespace(generator=FakeWorker()),0))
    def test_replaced_running_worker_is_retained_until_native_finished(self):
        class Signal:
            def __init__(self): self.callback = None
            def connect(self, fn): self.callback = fn
            def emit(self): self.callback()
        class Thread(FakeWorker):
            def __init__(self):
                super().__init__()
                self.finished = Signal()
        w = Thread()
        obj = SimpleNamespace(task_worker=w, _background_workers=[])
        retain_replaced_worker(obj, 'task_worker', None)
        obj.task_worker = None
        self.assertEqual(active_workers(obj), [w])
        w.running = False
        w.finished.emit()
        self.assertEqual(obj._background_workers, [])
        self.assertEqual(active_workers(obj), [])

    def test_drains_finished(self):
        class App:
            def processEvents(self): pass
        self.assertTrue(drain_finished_workers(App(),SimpleNamespace(generator=FakeWorker(False)),0))

if __name__=='__main__':unittest.main()
