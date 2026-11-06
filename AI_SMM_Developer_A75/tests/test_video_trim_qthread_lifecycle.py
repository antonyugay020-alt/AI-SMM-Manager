"""Regression for Qt native video worker: scheduling must never shadow QThread.start."""
from pathlib import Path
import tempfile
import time
import unittest
from PySide6.QtCore import QCoreApplication
from app import VideoTrimWorker


class NativeVideoWorkerNameSafety(unittest.TestCase):
    def test_video_trim_start_is_qthread_method_not_clip_position(self):
        with tempfile.TemporaryDirectory() as d:
            worker = VideoTrimWorker(Path(d)/"source.mp4",
                                     Path(d)/"clip.mp4", .25, 1.5)
            self.assertTrue(callable(worker.start))
            self.assertEqual(worker.clip_start, .25)
            self.assertEqual(worker.clip_end, 1.5)

    def test_invalid_range_terminates_without_outfile(self):
        application = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as d:
            output = Path(d)/"clip.mp4"
            worker = VideoTrimWorker(Path(d)/"source.mp4", output, 2, 1)
            failures, successes = [], []
            worker.failed.connect(failures.append)
            worker.done.connect(successes.append)
            worker.start()
            deadline = time.monotonic() + 10
            while worker.isRunning() and time.monotonic() < deadline:
                application.processEvents()
                time.sleep(.01)
            self.assertFalse(worker.isRunning())
            self.assertTrue(worker.wait(2000))
            application.processEvents()
            self.assertEqual(len(failures), 1)
            self.assertEqual(successes, [])
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
