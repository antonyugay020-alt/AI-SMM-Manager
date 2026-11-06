#!/usr/bin/env python3
"""Strict, external Windows acceptance supervisor for AI SMM Manager.
Run from the PARENT directory of the extracted candidate, or pass its path.
Does not modify candidate sources. Never treats fast/offline tests as release evidence.
"""
import argparse
import zipfile
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

REQUIRED = (
    'package_integrity', 'windows_platform', 'developer_test_source', 'unit_tests', 'live_qt_gui',
    'ollama_roundtrip', 'ollama_structured_json', 'ollama_constrained_schema',
    'ollama_inventory_map_schema', 'reviewer_models',
    'live_backend_content_pipeline', 'independent_semantic_acceptance',
    'live_gui_full_workflow', 'full_real_task_end_to_end', 'evidence_integrity',
)

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for b in iter(lambda: stream.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def assess(report, fingerprint):
    if not isinstance(report,dict) or report.get('status')!='PASS':
        return 'Overall acceptance is not PASS'
    checks=report.get('checks')
    if not isinstance(checks,dict): return 'Missing checks'
    for name in REQUIRED:
        if not isinstance(checks.get(name),dict) or checks[name].get('status')!='PASS':
            return 'Mandatory check not PASS: '+name
    completed=checks['full_real_task_end_to_end']
    if completed.get('profile')!='full' or report.get('test_profile')!='full': return 'Not the full acceptance profile'
    if checks['unit_tests'].get('profile')!='full': return 'Unit tests did not use full profile'
    if report.get('task_seed_replay_requested'): return 'Replayed test tasks do not count as new runs'
    if not isinstance(report.get('task_generation_seed'),int): return 'Missing randomized task seed'
    recorded=report.get('attested_source_fingerprint')
    if not isinstance(recorded,str) or not recorded:
        return 'No attested source fingerprint'
    if fingerprint is not None and recorded!=fingerprint:
        return 'Source fingerprint changed across passes'
    return None

def run(candidate, output, python, count=10, tests_source=None):
    candidate=Path(candidate).resolve()
    output=Path(output).resolve()
    if os.name!='nt':
        raise RuntimeError('Full Windows E2E acceptance requires Windows; no simulated PASS')
    if count != 10:
        raise ValueError('Release acceptance requires exactly 10 consecutive passes')
    tests_source=Path(tests_source).resolve() if tests_source else candidate.parent/'AI_SMM_Developer_A75'/'tests'
    if not (tests_source/'a2_recovery_fixture.json').is_file() or not any(tests_source.glob('test_*.py')):
        raise FileNotFoundError('Missing adjacent developer tests: '+str(tests_source))
    original_script=candidate/'windows_acceptance.py'
    if not original_script.is_file(): raise FileNotFoundError(original_script)
    identity=candidate/'candidate_identity.py'
    if not identity.is_file(): raise FileNotFoundError(identity)
    expected_folder=None
    for node in ast.parse(identity.read_text(encoding='utf-8')).body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='EXPECTED_FOLDER' for t in node.targets):
            expected_folder=ast.literal_eval(node.value)
    if not isinstance(expected_folder,str) or not expected_folder or Path(expected_folder).name!=expected_folder:
        raise ValueError('Invalid EXPECTED_FOLDER in candidate identity')
    if output==candidate or candidate in output.parents or output in candidate.parents:
        raise ValueError('Evidence directory must be outside the candidate folder')
    output.mkdir(parents=True,exist_ok=True)
    # Create a clean, correctly named folder; do not alter the user's original sources.
    staging=output/'candidate'/expected_folder
    if staging.exists():
        raise FileExistsError('Staging folder already exists: '+str(staging))
    staging.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(candidate,staging,ignore=shutil.ignore_patterns(
        '__pycache__','.pytest_cache','VALIDATION_RESULTS','VALIDATION_RESULTS.zip'))
    candidate=staging
    staged_tests=staging.parent/'AI_SMM_Developer_A75'/'tests'
    shutil.copytree(tests_source,staged_tests,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','*.pyc'))
    script=candidate/'windows_acceptance.py'
    summary={'status':'BLOCKED','clean_consecutive':0,'required':10,
             'candidate':str(candidate),'runs':[],
             'started_utc':datetime.now(timezone.utc).isoformat()}
    expected_fingerprint=None
    used_seeds=set()
    def save():
        (output/'TEN_RUN_STATUS.json').write_text(
            json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    for i in range(1,11):
        run_dir=output/f'run_{i:02d}'
        run_dir.mkdir(exist_ok=False)
        # The Windows tool itself creates VALIDATION_RESULTS.zip and acceptance.json.
        # Clear stale evidence before each run to prevent reuse of prior success.
        for stale in (candidate/'VALIDATION_RESULTS.zip', candidate/'VALIDATION_RESULTS'/'acceptance.json'):
            if stale.exists(): stale.unlink()
        env=os.environ.copy()
        env['AI_SMM_TEST_PROFILE']='full'
        env['AI_SMM_TEST_SOURCE']=str(staged_tests)
        env.pop('AI_SMM_ACCEPTANCE_SEED',None)  # permit freshly randomized tasks
        result=subprocess.run([str(python), str(script)],cwd=candidate,env=env,
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              text=True,errors='replace')
        (run_dir/'console.log').write_text(result.stdout,encoding='utf-8')
        report_path=candidate/'VALIDATION_RESULTS'/'acceptance.json'
        archive=candidate/'VALIDATION_RESULTS.zip'
        detail={'run':i,'exit_code':result.returncode,'status':'BLOCKED'}
        if archive.is_file():
            shutil.copy2(archive,run_dir/'VALIDATION_RESULTS.zip')
            detail['report_sha256']=sha256(run_dir/'VALIDATION_RESULTS.zip')
        if result.returncode == 0 and report_path.is_file() and archive.is_file():
            try:
                report=json.loads(report_path.read_text(encoding='utf-8-sig'))
                with zipfile.ZipFile(archive) as evidence_zip:
                    archived=json.loads(evidence_zip.read('VALIDATION_RESULTS/acceptance.json').decode('utf-8-sig'))
                if archived != report:
                    raise ValueError('Evidence ZIP does not match on-disk acceptance report')
                error=assess(report,expected_fingerprint)
                if error is None and report['task_generation_seed'] in used_seeds:
                    error='Duplicate randomized task seed'
                if error is None:
                    used_seeds.add(report['task_generation_seed'])
                    expected_fingerprint=report['attested_source_fingerprint']
                    detail['status']='PASS'
                    detail['fingerprint']=expected_fingerprint
                else: detail['reason']=error
            except (OSError,ValueError,KeyError,TypeError) as exc:
                detail['reason']='Invalid evidence: '+str(exc)
        else:
            detail['reason']='Process failed or evidence missing'
        summary['runs'].append(detail)
        if detail['status']!='PASS':
            summary['clean_consecutive']=0
            summary['status']='BLOCKED'
            save()
            print(f'BLOCKED on run {i}; counter reset to 0/10. See {run_dir}')
            return 2
        summary['clean_consecutive']=i
        save()
        print(f'FULL PASS {i}/10')
    summary['status']='PASS'
    summary['finished_utc']=datetime.now(timezone.utc).isoformat()
    save()
    return 0

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('candidate',help='Extracted AI SMM candidate folder')
    p.add_argument('--output',default='AI_SMM_TEN_FULL_QA_RESULTS')
    p.add_argument('--tests-source',default=None)
    p.add_argument('--python',default=sys.executable)
    args=p.parse_args()
    try: code=run(args.candidate,args.output,args.python,tests_source=args.tests_source)
    except Exception as exc:
        print('BLOCKED:',exc,file=sys.stderr)
        code=2
    raise SystemExit(code)
