"""Small offline Windows ownership probe. Never a full application acceptance."""
from pathlib import Path
import argparse
import sys

ROOT=Path(__file__).resolve().parents[1]
MODULES=('test_native_log_budget.py','test_windows_tree_drain.py','test_media_spool_lease.py')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    args.report.mkdir(parents=True,exist_ok=True)
    import pytest
    return pytest.main(['-q','--tb=short','--junitxml='+str(args.report/'process_tests.xml'),
        *[str(ROOT/'ci'/name) for name in MODULES]])


if __name__=='__main__':raise SystemExit(main())
