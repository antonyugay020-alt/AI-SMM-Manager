#!/usr/bin/env python3
"""Fail-closed structural preflight; does not certify application correctness."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / 'AI_SMM_Work_Architecture_A75'
DEV = ROOT / 'AI_SMM_Developer_A75'
TESTS = DEV / 'tests'
REQUIRED = (
    PROD / 'app.py', PROD / 'release_guard.py', PROD / 'windows_acceptance.py',
    PROD / 'RUN_AI_SMM_MANAGER.cmd', PROD / 'requirements.txt',
    DEV / 'app.py', TESTS / 'conftest.py',
    ROOT / 'AI_SMM_10_FULL_WINDOWS_QA.py', ROOT / 'RUN_10_FULL_WINDOWS_QA.cmd',
)


def ordered_snapshot_paths(paths):
    """Order by exact repository-relative POSIX name, never OS Path ordering.

    PureWindowsPath compares case-insensitively and PurePosixPath compares
    case-sensitively. Raw Path sorting created divergent SHA-256 on identical
    Git source snapshots. This key preserves exact bytes and detects drift.
    """
    return sorted(paths, key=lambda p: p.relative_to(ROOT).as_posix())


def files_snapshot():
    """Fingerprint current code and test assets, never generated reports or credentials."""
    files = []
    for base in (PROD, DEV):
        for path in sorted(base.rglob('*')):
            if not path.is_file() or any(part in {'.git', '__pycache__', '.pytest_cache', '.venv'} for part in path.parts):
                continue
            if path.suffix.lower() not in {'.py', '.json', '.txt', '.md', '.cmd', '.ini'}:
                continue
            files.append(path)
    files.extend([ROOT / 'AI_SMM_10_FULL_WINDOWS_QA.py', ROOT / 'RUN_10_FULL_WINDOWS_QA.cmd'])
    digest = hashlib.sha256()
    hashes = {}
    for path in ordered_snapshot_paths(files):
        rel = path.relative_to(ROOT).as_posix()
        content = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[rel] = content
        digest.update(rel.encode('utf-8') + b'\0' + content.encode('ascii') + b'\n')
    return digest.hexdigest(), hashes


def verify():
    errors = []
    for file in REQUIRED:
        if not file.is_file(): errors.append('missing: ' + str(file.relative_to(ROOT)))
    prod = {p.name: p for p in PROD.glob('*.py')}
    dev = {p.name: p for p in DEV.glob('*.py') if p.name != 'conftest.py'}
    if prod.keys() != dev.keys():
        errors.append('production/developer module names differ: ' + repr(sorted(prod.keys() ^ dev.keys())))
    for name in sorted(prod.keys() & dev.keys()):
        if prod[name].read_bytes() != dev[name].read_bytes():
            errors.append('production/developer module drift: ' + name)
    # This repository carries a third, importable copy of its runtime under
    # Developer/tests for cross-platform QA. Treat it as a production mirror,
    # not as a second implementation; catch drift before thousands of tests.
    mirrors = {p.name: p for p in TESTS.glob('*.py') if p.name in dev}
    missing_mirrors = sorted(dev.keys() - mirrors.keys())
    if missing_mirrors:
        errors.append('missing test runtime mirrors: ' + repr(missing_mirrors))
    for name in sorted(dev.keys() & mirrors.keys()):
        if dev[name].read_bytes() != mirrors[name].read_bytes():
            errors.append('developer/test runtime drift: ' + name)
    modules = sorted(TESTS.glob('test_*.py'))
    if len(modules) < 250:
        errors.append(f'test corpus truncated: only {len(modules)} modules')
    # Do not accidentally package local credentials or sensitive runtime state.
    for base in (ROOT, PROD, DEV):
        for pattern in ('*.env', '.env', '*private_key*.pem', 'client_secret.json', '*.sqlite', '*.sqlite3'):
            for path in base.glob(pattern):
                if path.is_file(): errors.append('sensitive runtime artifact present: ' + path.relative_to(ROOT).as_posix())
    fingerprint, file_hashes = files_snapshot()
    result = {
        'status': 'PASS' if not errors else 'BLOCKED',
        'fingerprint_sha256': fingerprint,
        'source_python_modules': len(prod),
        'developer_test_modules': len(modules),
        'file_hashes_count': len(file_hashes),
        'errors': errors,
        'scope': 'structure, code/test mirror and source bytes; not model or Windows GUI acceptance',
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'ci_evidence'/'tree.json')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = verify()
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report['status'] == 'PASS' else 2

if __name__ == '__main__': raise SystemExit(main())
