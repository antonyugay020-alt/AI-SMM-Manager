#!/usr/bin/env python3
"""Verify cross-platform evidence on the exact same source snapshot.
Skip count is explicitly preserved; full live acceptance is a different gate.
"""
import argparse
import json
from pathlib import Path
import sys

REQUIRED={'linux','win32'}

def assess(paths,required_shards=4):
    found={}
    errors=[]
    fingerprints=set()
    for path in paths:
        r=json.loads(path.read_text(encoding='utf-8'))
        key=(r.get('platform'),r.get('shard'))
        if key in found: errors.append(f'duplicate evidence {key}')
        found[key]=r
        fingerprints.add(r.get('source_fingerprint'))
        if r.get('status')!='PASS': errors.append(f'blocked shard {key}')
        if r.get('shards')!=required_shards:errors.append(f'unexpected shards at {key}')
        if not isinstance(r.get('counts'),dict) or r['counts'].get('tests',0)<1:
            errors.append(f'missing executed tests {key}')
    expected={(system,index) for system in REQUIRED for index in range(required_shards)}
    if set(found)!=expected:errors.append(f'platform/shard coverage mismatch: missing={sorted(expected-set(found))}, extra={sorted(set(found)-expected)}')
    if len(fingerprints)!=1 or None in fingerprints:errors.append('source fingerprints are missing or differ')
    for system in REQUIRED:
        inventory={found.get((system,i),{}).get('total_module_inventory') for i in range(required_shards)}
        counted=sum(found.get((system,i),{}).get('module_count',0) for i in range(required_shards))
        sole=next(iter(inventory)) if len(inventory)==1 else None
        if not isinstance(sole,int) or sole<250 or counted!=sole:
            errors.append(f'{system} module coverage incomplete')
    total_tests=sum(x.get('counts',{}).get('tests',0) for x in found.values() if isinstance(x.get('counts'),dict))
    total_skips=sum(x.get('counts',{}).get('skipped',0) for x in found.values() if isinstance(x.get('counts'),dict))
    return {'status':'PASS' if not errors else 'BLOCKED', 'error_details':errors,
            'cross_platform_test_executions':total_tests,'skipped_tests':total_skips,
            'source_fingerprint':next(iter(fingerprints)) if len(fingerprints)==1 else None,
            'scope':'Linux/Windows code regression only; no live models or interactive Windows Qt E2E'}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path,default=Path('downloaded_evidence'))
    p.add_argument('--output',type=Path,default=Path('ci_evidence/aggregate.json'))
    a=p.parse_args()
    files=list(a.input.rglob('*_shard_*.json'))
    result=assess(files)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0 if result['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
