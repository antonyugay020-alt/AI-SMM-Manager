"""Real local reviewer controls only. No model installation or acceptance credit."""
import argparse
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if os.environ.get('AI_SMM_QA_LOCAL_ONLY')!='1':
        raise RuntimeError('Use RUN_WINDOWS_REVIEWER_QA.cmd: explicit local-only roster required')
    from windows_qa_contract import write_json
    import windows_acceptance as acceptance
    acceptance.RESULT={'checks':{},'scope':'reviewer_qualification_diagnostic',
        'acceptance_run':False,'release_eligible':False,'target_clean_consecutive':0}
    try:
        passed=acceptance.reviewer_model_probe()
        acceptance.RESULT['status']='PASS' if passed else 'BLOCKED'
    except Exception as exc:
        passed=False
        acceptance.RESULT.update(status='BLOCKED',error=type(exc).__name__+': '+str(exc))
    write_json(args.report/'reviewer_qualification.json',acceptance.RESULT)
    return 0 if passed else 2


if __name__=='__main__':raise SystemExit(main())
