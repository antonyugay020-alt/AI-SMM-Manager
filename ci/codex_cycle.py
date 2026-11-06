#!/usr/bin/env python3
"""Run Codex on an ephemeral CI checkout. Never pushes or modifies main itself."""
from pathlib import Path
import os
import subprocess
import sys
import json

ROOT = Path(__file__).resolve().parents[1]

PROMPT = '''AI SMM Manager architecture integration task.
Work only in this ephemeral Git checkout and fix shared architectural causes, not single-case patches.
Requirements in AI_SMM_Work_Architecture_A75/PROJECT_VISION.md, AGENTS.md and RELEASE_POLICY.md are authoritative.
Do not weaken or edit release_guard.py, windows_acceptance.py, CI scripts, GitHub workflows, QA supervisor, security checks or acceptance thresholds.
When modifying a production Python module under AI_SMM_Work_Architecture_A75, synchronize its identical mirror in AI_SMM_Developer_A75.
Keep all outstanding projects/data isolation, transactional safety, independent evaluation, Windows Qt lifecycle, complete execution and recovery.
Add regression cases for confirmed defects; do not fabricate PASS, remove tests, or suppress failures.
Produce code changes only; this branch will be independently tested and reviewed and may be rejected.
Specific task:\n'''


def main():
    task=os.environ.get('SMM_CODEX_TASK','').strip()
    if not task: print('BLOCKED: missing specific task',file=sys.stderr);return 2
    if not os.environ.get('OPENAI_API_KEY'):
        print('BLOCKED: OPENAI_API_KEY is missing in GitHub Secrets',file=sys.stderr);return 2
    output=ROOT/'ci_evidence'
    output.mkdir(exist_ok=True)
    log=output/'codex_output.log'
    status={'status':'BLOCKED','agent':'codex','phase':'coding','exit_code':None}
    # Do not log the prompt, API keys or environment variables.
    command=['codex','exec','--sandbox','workspace-write',PROMPT+task]
    try:
        with log.open('w',encoding='utf-8') as stream:
            result=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,
                                  timeout=5400,check=False)
        status['exit_code']=result.returncode
        if result.returncode != 0:raise RuntimeError('Codex exited with an error')
        status['status']='PASS'
    except (OSError,RuntimeError,subprocess.TimeoutExpired) as exc:
        status['reason']=str(exc)
    (output/'codex.json').write_text(json.dumps(status,indent=2)+'\n',encoding='utf-8')
    print('Codex execution:',status['status'],'exit:',status.get('exit_code'))
    if status['status']!='PASS':
        print('Full diagnostics retained as restricted CI artifact (inspect for secrets before sharing)')
    return 0 if status['status']=='PASS' else 2

if __name__=='__main__': raise SystemExit(main())
