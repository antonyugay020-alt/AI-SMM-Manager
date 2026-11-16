"""Native Qt contract smoke, including on Windows CI; synthetic AI, no release claim.

Separate processes keep QApplication and application module globals out of the
regression interpreter and prove that restart reads durable state.
"""
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest


class NativeQtLifecycleSmoke(unittest.TestCase):
    def test_randomized_tasks_and_restart_in_native_application(self):
        tests=Path(__file__).resolve().parent
        helper=tests/'qt_scenarios'/'native_lifecycle.py'
        seed=secrets.randbits(64)
        with tempfile.TemporaryDirectory() as home:
            env=os.environ.copy()
            env['AI_SMM_TEST_ROOT']=home
            env['QT_QPA_PLATFORM']='offscreen'
            env['PYTHONUTF8']='1'
            env['PYTHONPATH']=os.pathsep.join((str(tests.parent),str(tests)))
            for phase in ('execute','restart'):
                result=subprocess.run([sys.executable,str(helper),phase,str(seed)],
                    env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
                self.assertEqual(result.returncode,0,
                    f'Native Qt synthetic smoke seed={seed} phase={phase}\n{result.stdout}\n{result.stderr}')
                self.assertIn(f'NATIVE_QT_SYNTHETIC_{phase.upper()}_OK',result.stdout)
