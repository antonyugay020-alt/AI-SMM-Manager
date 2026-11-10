# Native initialization, ownership and independent reviewer integration

Baseline `c6d1ec892b9409968d8ce5ba53cdaf45f45858d2`, existing integration branch
and draft PR #4. Resulting source is the commit containing this checkpoint.
Tested runtime fingerprint:
`3e7b82e87c7e7ea0abd3f81540ced051413cf8497fda384de9a92907720f2125`.
`tested-bytes.json` binds all runtime/test mirrors and additional root/CI source.

## Evidence and architectural causes

The recorded actual Windows evidence remains the f967fdd 3080 Ti failure matrix
in DEVELOPMENT_STATE.md: developer/CI/Qt checks passed on that OLD source;
image case 0 stopped at importing_torch for 480.1 seconds, despite a successful
independent bare-Python preflight. There was 10086 MiB free before launch.
The five unsuccessful reviewer verdicts were semantic/contract failures, not
transport timeouts; only deepseek-r1 passed all six controls. There is no new
Windows execution in this Work phase and no proof that the old stall has been
physically eliminated. No new raw Windows ZIP was supplied to this turn; the
recorded matrix is distinguished from the historical first-run attachment.

Full baseline checkout regression found a broken real parent-EOF fixture: it
sent an empty request although main now verifies generation before import.
That fixture was corrected without bypassing the real EOF termination test.
Beyond that mismatch, the audit found connected lifecycle and admission defects:

1. Preflight used a bare import command, while generation inherited the GUI's
   Python environment, loaded Pillow earlier and started a Python observer
   before importing Torch. Their successful/failed results were not comparable.
   Both now launch the same native runner via isolated Python (-I, -u, UTF-8),
   without inherited PYTHONPATH/HOME/startup/user-site overrides. Trusted source
   imports are explicit. Torch initializes before Pillow, Python observer threads
   and GPU ownership; Pillow and the exact Diffusers pipeline classes initialize
   afterward inside the common device lease. Preflight and inference use the
   same instrumented phases. Native stack dumps cover Torch and framework
   imports; stage budgets remain bounded. No duplicate-OpenMP workaround,
   global DLL/environment change, CPU fallback or model download was added.
2. A Python EOF observer cannot execute while a blocked native call holds the
   GIL. New native children are therefore contained BEFORE application startup:
   Windows suspended launch/assigned kill-on-close Job Object, Linux parent-death
   signal plus parent-PID race check. The supplemental EOF observer starts only
   after CPU native initialization. Only children created by this application
   are controlled; no enumeration/name-based kill or trading-process action.
3. Cleanup after a terminate/exit race could raise before kill escalation. A
   reproducing fault test failed before the correction (log retained here).
   Central owned-process cleanup now retains bounded wait/kill escalation and
   closes the liveness pipe. Duplicate generation cleanup was removed; it cannot
   replace cancellation with an unrelated cleanup exception. Real parent death
   of a child occupied by a GIL-holding native call is tested on the actual OS.
4. Native preflight now validates complete typed hardware/dependency receipts,
   rejects partial/contradictory output, retains bounded diagnostics on timeout
   or import failure, and puts its records inside the Windows report folder.
   Repeated cases reuse each verified Python's preflight within the run, avoiding
   redundant interpreter/CUDA starts. CUDA checks/model loading/inference remain
   within the shared lease; native startup is not a model-quality PASS.
5. Qwen 2.5 Coder and base 14B could be treated as independent reviewer families.
   They now share their foundation identity. An unsuccessful first variant also
   used to hide other variants before qualification. Selection now tries each
   eligible tag at most once until enough qualified families exist, counting a
   family only after a real PASS. Thus failed Coder does not hide 14B, and Coder
   does not become a second independent vote for that same foundation. The six
   semantic controls, no-author-family rule, one-call qualification, explicit
   panel authority and all quality gates are unchanged. No actual Qwen14B PASS
   or installed-tag inventory is fabricated from offline capacity fixtures.

## Final available verification

All execution below is Linux/CPU/Qt offscreen. Logs and JUnit are beside this
checkpoint; they do not advance Windows acceptance.

| Check | Final result |
| --- | --- |
| All 263 developer modules | 2109 passed + 1049 subtests; no failures/skips |
| Actual isolated four-shard QA layout (66/66/66/65) | 2109 passed + 1048 subtests; no failures/skips |
| Complete CI | 340 passed + 77 subtests; 2 native-Windows-only skips |
| Complete CI with forced GC per case | Same 340 + 77, 2 skips |
| Real isolated native preflight, installed Torch/Diffusers | PASS, Torch 2.8.0+cpu, CUDA false (not CUDA acceptance) |
| Real cached tiny CPU instruction generation through Qt/SQLite | PASS, including cancel, owner crash, child exit, same-ID retry, reopen and receipt adoption |
| Real cached tiny AnimateDiff video through Qt/SQLite | PASS, including original audio |
| Source/test mirrors and dependency graph | PASS, 164 shared runtime modules |
| Tested-byte manifest and diff checks | PASS |

Native lifecycle checks include real isolated-interpreter startup, rejection of
inherited Python startup paths, native-import timeout with retained evidence,
actual parent death while native code holds the GIL, and controlled cleanup
fault escalation. Windows Job Object behavior is not claimed from Linux.
The other two image engines' interface/mask/reference contracts are covered by
CI fixtures and real installed framework imports. Their trained bundles are not
available here; no actual trained inpaint/IP-Adapter quality is certified.
Cached tiny neural models prove topology/lifecycle only. No live local Ollama
reviewer qualification was run in Work; previous Windows FAILs remain FAIL.

Commands (use /workspace/work/venv/bin/python for python):

```sh
AI_SMM_TEST_ROOT=/workspace/work/pr4-c6d1ec8/frozen-dev-data QT_QPA_PLATFORM=offscreen python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/pr4-c6d1ec8/frozen-regression
AI_SMM_TEST_ROOT=/workspace/work/pr4-c6d1ec8/frozen-ci2-data QT_QPA_PLATFORM=offscreen python -m pytest -q ci --tb=short --junitxml=/workspace/work/pr4-c6d1ec8/final-ci.xml
python /workspace/work/pr4-c6d1ec8/run_isolated.py
AI_SMM_TEST_ROOT=/workspace/work/pr4-c6d1ec8/gc-data QT_QPA_PLATFORM=offscreen python /workspace/work/pr4-c6d1ec8/run_gc.py
PYTHONPATH=AI_SMM_Work_Architecture_A75 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python -c "import os;from native_process_runtime import probe_native_runtime;print(probe_native_runtime('/workspace/work/generative/venv/bin/python',os.environ,diagnostic_path='/workspace/work/pr4-c6d1ec8/actual-native-preflight.json'))"
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-profiles.json --profile tiny-topology-only --lifecycle
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-video-profiles.json --profile tiny-video-topology-only
python ci/verify_tree.py
git diff --check
```

Baseline CI failed one stale request fixture (331 PASS/1 FAIL/2 SKIP). A first
refactor test accidentally mocked subprocess globally, intercepting real
ffprobe; the fixture now replaces only generation's owned child context. The
cleanup race regression also demonstrated a genuine missing escalation before
fixing it. None of these failed iterations is counted as PASS. All final checks
above followed the last runtime modification and use the recorded source bytes.

## Remaining physical gates

Cloud verification is complete for the stated available scope. Physical
Windows Qt/CUDA startup, trained-model quality and current installed reviewer
admission remain unverified. In particular, the exact cause of the old Windows
Torch stall is not proven without a native trace/new execution. The startup
ordering differential, inherited paths and GIL-dependent ownership defects have
been addressed and are independently testable; this is not a Windows PASS.

The existing one-launch Windows QA remains RUN_WINDOWS_QA.cmd (phase 1, 3080 Ti).
It uses the revised native preflight, retains its diagnostics, qualifies selected
installed reviewers (including the exact Qwen2.5 14B tag if present/selected),
and applies the unchanged independence/semantic gates. Report root remains
%USERPROFILE%\Desktop\AI_SMM_REPORTS; existing junction/history is preserved.
No additional Windows run was requested before cloud checks passed.

Target RTX 2080 Super 8 GB still requires ten consecutive clean full Windows
Qt/CUDA runs on the frozen composition after preliminary PASS; any defect or
source/model/input drift resets the counter. Current target count remains 0/10.
Main, trading bots and monitors were not modified. No Actions, paid APIs,
model downloads, merge, user build or release was performed.
