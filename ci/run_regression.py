#!/usr/bin/env python3
"""Deterministically run exactly one of four independently reported test shards.
Never reclassify timeout, missing evidence or collection-only as PASS.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from verify_tree import verify, files_snapshot

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT/'AI_SMM_Developer_A75'
TESTS = DEV/'tests'


def shard_modules(shards: int, index: int):
    if shards < 1 or not 0 <= index < shards:
        raise ValueError('invalid shard')
    modules = sorted(TESTS.glob('test_*.py'))
    if len(modules) < 250:
        raise ValueError(f'incomplete test inventory: {len(modules)}')
    groups = [modules[i::shards] for i in range(shards)]
    if sorted(x for group in groups for x in group) != modules:
        raise ValueError('test inventory is not a partition')
    return [x.relative_to(DEV).as_posix() for x in groups[index]], len(modules)


def junit_counts(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else root.findall('testsuite')
    return {key: sum(int(node.get(key, 0)) for node in suites) for key in ('tests','errors','failures','skipped')}


def execute(shards: int, index: int, timeout: int, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    before = verify()
    selected,total = shard_modules(shards,index)
    report = {'status':'BLOCKED', 'platform':sys.platform, 'shard':index,
              'shards':shards, 'module_count':len(selected),
              'total_module_inventory':total,
              'source_fingerprint':before['fingerprint_sha256'],
              'command':'python -m pytest --import-mode=importlib -q <explicit module list>',
              'counts': None, 'reason': None}
    path=out/f'{sys.platform}_shard_{index}.json'
    log=out/f'{sys.platform}_shard_{index}.log'
    xml=out/f'{sys.platform}_shard_{index}.xml'
    try:
        if before['status'] != 'PASS': raise RuntimeError('structural preflight blocked: '+repr(before['errors']))
        env = os.environ.copy()
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['QT_QPA_PLATFORM'] = env.get('QT_QPA_PLATFORM', 'offscreen')
        env['PYTHONPATH'] = os.pathsep.join([str(DEV),str(TESTS),env.get('PYTHONPATH','')])
        command=[sys.executable,'-m','pytest','--import-mode=importlib','-q',
                 '--junitxml='+str(xml),*selected]
        with log.open('w', encoding='utf-8') as stream:
            result = subprocess.run(command,cwd=DEV,env=env,
                                    stdout=stream,stderr=subprocess.STDOUT,
                                    timeout=timeout, check=False)
        report['exit_code']=result.returncode
        if not xml.exists(): raise RuntimeError('pytest did not produce JUnit evidence')
        counts=junit_counts(xml)
        report['counts']=counts
        if result.returncode != 0 or counts['errors'] or counts['failures'] or counts['tests'] < 1:
            raise RuntimeError(f'pytest status {result.returncode}; counts={counts}')
        after=verify()
        if after['fingerprint_sha256'] != before['fingerprint_sha256']:
            raise RuntimeError('source/test snapshot changed during regression')
        report['status']='PASS'
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired,ET.ParseError) as exc:
        report['reason']=str(exc)
    finally:
        path.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        print(json.dumps(report,indent=2,ensure_ascii=False))
        if log.exists():
            print('Last test-log lines:')
            print('\n'.join(log.read_text(encoding='utf-8',errors='replace').splitlines()[-12:]))
    return 0 if report['status']=='PASS' else 2


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--shards',type=int,default=4)
    ap.add_argument('--index',type=int,required=True)
    ap.add_argument('--timeout',type=int,default=3000)
    ap.add_argument('--output',type=Path,default=ROOT/'ci_evidence')
    a=ap.parse_args()
    return execute(a.shards,a.index,a.timeout,a.output)

if __name__=='__main__': raise SystemExit(main())
