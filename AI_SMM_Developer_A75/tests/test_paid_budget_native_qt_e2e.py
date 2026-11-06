"""Real offscreen Qt widget interactions and SQLite budget persistence across processes.

Synthetic UI regression only: not the ten required real-model Windows E2E runs.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class NativeBudgetQtTests(unittest.TestCase):
    def test_edit_budget_and_reopen_dialog_after_process_restart(self):
        root=Path(__file__).resolve().parent
        helper=root/'qt_scenarios'/'paid_budget_native.py'
        with tempfile.TemporaryDirectory() as home:
            env=os.environ.copy()
            env['AI_SMM_TEST_ROOT']=home
            env['QT_QPA_PLATFORM']='offscreen'
            env['PYTHONUTF8']='1'
            env['PYTHONPATH']=os.pathsep.join((str(root.parent),str(root)))
            for phase in ('configure','restart'):
                result=subprocess.run([sys.executable,str(helper),phase],
                    env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
                self.assertEqual(result.returncode,0,
                    f'Native Qt budget phase={phase}\n{result.stdout}\n{result.stderr}')
                self.assertIn('NATIVE_QT_BUDGET_'+phase.upper()+'_OK',result.stdout)


if __name__=='__main__':
    unittest.main()
