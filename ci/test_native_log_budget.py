"""Bounded native QA/preflight logs; actual owned CPU children, no GPU or network.

A noisy or stalled local native process must not fill disk, block the Qt
event pump, or leave an owned child alive after rejection. Synthetic children
do NOT certify model quality or Windows CUDA/Job Object acceptance.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
import native_process_runtime as native


class NativeLogBudgetTests(unittest.TestCase):
    def _child(self,script_text,*,timeout=5,max_log_bytes=4096,pump=None):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            script=root/'child.py'
            script.write_text(script_text,encoding='utf-8')
            with (root/'child.log').open('wb') as log:
                with native.owned_native_process(
                        native.native_command(sys.executable,script),
                        env=os.environ,stdout=log,stderr=log) as child:
                    return native.wait_owned_native(
                        child,log,timeout=timeout,pump=pump,
                        max_log_bytes=max_log_bytes)

    def test_normal_exit_and_nonzero_exit_are_reported(self):
        self.assertEqual(self._child("print('ok',flush=True)"),0)
        self.assertEqual(self._child("import sys;sys.exit(7)"),7)

    def test_flooding_child_is_stopped_before_its_deadline(self):
        with self.assertRaisesRegex(RuntimeError,'NATIVE_LOG_BUDGET_EXCEEDED'):
            self._child("import sys,time;sys.stdout.write('x'*262144);"
                        "sys.stdout.flush();time.sleep(30)",timeout=5)

    def test_hanging_child_is_stopped_and_reaped(self):
        with self.assertRaisesRegex(TimeoutError,'NATIVE_PROCESS_TIMEOUT'):
            self._child('import time;time.sleep(30)',timeout=.3)

    def test_event_pump_failure_unwinds_child_owner(self):
        class Interrupted(Exception):pass
        def pump():raise Interrupted()
        with self.assertRaises(Interrupted):
            self._child('import time;time.sleep(30)',pump=pump)

    def test_invalid_deadline_and_budget_rejected(self):
        with tempfile.TemporaryFile() as log:
            class Finished:
                def poll(self):return 0
            for timeout in (0,True,float('nan'),float('inf'),3601):
                with self.subTest(timeout=timeout):
                    with self.assertRaises(ValueError):
                        native.wait_owned_native(Finished(),log,timeout=timeout)
            for budget in (0,True,-1,16*1024*1024+1):
                with self.subTest(budget=budget):
                    with self.assertRaises(ValueError):
                        native.wait_owned_native(Finished(),log,timeout=1,max_log_bytes=budget)
            with self.assertRaises(TypeError):
                native.wait_owned_native(Finished(),log,timeout=1,pump='invalid')

    def test_preflight_flood_is_blocked_with_diagnostic_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            script=root/'flood.py'
            script.write_text("import sys,time;sys.stdout.write('x'*262144);"
                              "sys.stdout.flush();time.sleep(30)",encoding='utf-8')
            command=native.native_command(sys.executable,script)
            with patch.object(native,'native_command',return_value=command),patch.object(
                    native,'MAX_NATIVE_LOG_BYTES',4096):
                with self.assertRaises(native.NativeRuntimeError) as raised:
                    native.probe_native_runtime(sys.executable,os.environ,
                        timeout=5,diagnostic_path=root/'preflight.json')
            report=json.loads((root/'preflight.json').read_text(encoding='utf-8'))
            self.assertEqual(report['error_code'],'NATIVE_PREFLIGHT_LOG_BUDGET')
            self.assertGreater(report['log_bytes'],4096)
            self.assertLessEqual(len(report['log_tail']),16384)
            self.assertEqual(raised.exception.diagnostic,report)

    def test_both_preflight_and_semantic_qa_share_the_same_budget(self):
        source=(ROOT/'AI_SMM_Work_Architecture_A75/qa_quality_worker.py').read_text(
            encoding='utf-8')
        self.assertIn('wait_owned_native(process,log,timeout=self.timeout,pump=pump)',source)
        self.assertNotIn('while process.poll() is None:',source)
        self.assertIn('exit_code=wait_owned_native(process,log,timeout=timeout)',
                      (ROOT/'AI_SMM_Work_Architecture_A75/native_process_runtime.py').read_text(
                          encoding='utf-8'))


if __name__=='__main__':
    unittest.main()
