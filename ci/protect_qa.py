#!/usr/bin/env python3
"""Reject Codex-generated changes to CI and release authority; trust only a clean base checkout."""
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
PROTECTED_PREFIXES=(
    '.github/', 'ci/',
    'AI_SMM_Work_Architecture_A75/release_guard.py',
    'AI_SMM_Developer_A75/release_guard.py',
    'AI_SMM_Work_Architecture_A75/windows_acceptance.py',
    'AI_SMM_Developer_A75/windows_acceptance.py',
    'AI_SMM_10_FULL_WINDOWS_QA.py', 'RUN_10_FULL_WINDOWS_QA.cmd',
)


def changed():
    proc=subprocess.run(['git','status','--porcelain=v1','--untracked-files=all','-z'],
                        cwd=ROOT,stdout=subprocess.PIPE,check=True)
    paths=[]
    for entry in proc.stdout.decode('utf-8','replace').split('\0'):
        if not entry: continue
        if len(entry) < 4: continue
        # Git status prints two status columns, space, then a path.
        path=entry[3:].replace('\\','/')
        paths.append(path)
    return paths


def main():
    bad=[p for p in changed() if p.startswith(PROTECTED_PREFIXES)]
    if bad:
        print('BLOCKED: agent attempted to modify protected QA/CI authority:',bad, file=sys.stderr)
        return 2
    print('Protected QA/CI files unchanged by code agent')
    return 0

if __name__=='__main__':raise SystemExit(main())
