# Windows process signals and short diagnostic — 2026-11-16

Baseline `07cf5d2ef3400b6bd60025c49557592519acc6b9`, same branch/PR #4.

## What the new physical report disproved

The prior ActiveProcesses drain did **not** resolve the native file-sharing
failures on Windows. The spool byte-lock assertion now passes. The two original
native timeout/log-budget cases still fail to remove child.log with WinError 32,
and the explicit nested-child inherited-output test also fails to unlink its log.
Therefore no successful Windows cleanup acceptance is claimed for 07cf5d2.

Windows CI: 581 passed + 94 subtests, 17 platform skips, 3 failures. All 263
developer modules: 2066 passed + 1058 subtests, 43 platform skips. The input source
manifest has 926 exact baseline matches plus 8 CMD CRLF equivalents, no other
changes. The archive hash and sanitized result inventory are in input-summary.json.
Raw user archive, original photos, databases and local personal paths are omitted.

RAM is now measured: 5,614,284,800 bytes available (5.23 GiB) at the generation
check versus the unchanged 6 GiB image floor; total RAM 34,277,433,344 bytes.
All five text candidates remain unqualified: Gemma3 3/6, Qwen3 4/6,
Qwen2.5-coder 3/6, Llama3.1 4/6 and DeepSeek 3/6. Completed responses are not
sufficient for admission. No quality oracle, memory floor or final verdict is relaxed.

## Revised ownership boundary

Job accounting alone was treated as proof that each process had completed.
The new implementation enumerates only the owned job's members, opens private
SYNCHRONIZE handles before termination, and waits for their actual process
signals within the existing deadline. It still terminates the whole job and
checks the active count. PID-list growth is bounded; member handles are closed
on success, error and timeout. An unsignalled process is a failure even if job
accounting is already zero. This closes a missing wait in the protocol; the
physical cause of remaining Windows handle retention is not declared fully
resolved until the new diagnostic runs on Windows.

Native owners retain bounded cleanup evidence (observed member PIDs and waited
PIDs). Failing native tests print it, so another failure is diagnosable rather
than inferred only from the accounting counter. No global process scan/kill,
forced file deletion, delay-and-ignore workaround, or skipping of failed cases.

## Short diagnostic handoff

RUN_WINDOWS_PROCESS_QA.cmd uses the prepared QA Python and existing report folder.
It runs only the native log-budget, process-tree and spool-lock test modules,
without model configuration, generation, Ollama or CUDA inference. The same
atomic DIAGNOSTICS.zip publication and history apply. Success is explicitly
DIAGNOSTIC_COMPLETE, **not** full application PASS. The target counter stays zero;
no preliminary attestation is issued. Failure to publish the archive is fatal.

## Validation performed on the final Linux source

```
QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python -m pytest -q ci/test_process_diagnostic_scope.py ci/test_windows_tree_drain.py ci/test_native_log_budget.py ci/test_media_spool_lease.py ci/test_windows_qa_source.py ci/test_windows_qa_environment.py --tb=short
AI_SMM_TEST_ROOT=/workspace/work/windows-qa-07cf5d2/dev-data QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/windows-qa-07cf5d2/developer
AI_SMM_TEST_ROOT=/workspace/work/windows-qa-07cf5d2/ci-data QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python -m pytest -q ci --tb=short --junitxml=/workspace/work/windows-qa-07cf5d2/ci.xml
AI_SMM_TEST_ROOT=/workspace/work/windows-qa-07cf5d2/gc-data QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python /workspace/work/windows-qa-07cf5d2/run_gc.py
QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python ci/windows_process_diagnostic.py --report /workspace/work/windows-qa-07cf5d2/short-diagnostic
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 /workspace/work/venv/bin/python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-profiles.json --profile tiny-topology-only --lifecycle
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 /workspace/work/venv/bin/python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-video-profiles.json --profile tiny-video-topology-only
/workspace/work/venv/bin/python ci/verify_tree.py --output /workspace/work/windows-qa-07cf5d2/tree.json
git diff --check
```

Focused: 59 passed + 9 subtests. All 263 developer modules: 2109 passed + 1062
subtests, no failures/skips. Complete CI and forced-GC CI each: 605 passed + 94
subtests, 2 Windows-only skips. Actual short diagnostic subprocess on Linux:
26 passed + 9 subtests (POSIX behavior and WinAPI doubles, not Windows evidence).
Initial focused test doubles lacked os.replace for archive publication; corrected
before the final suites. A return inside main's try was also corrected before
final verification so archive failure cannot preserve a successful exit code.

Actual cached tiny CPU Qt/Diffusers lifecycle is recorded separately; test-only
weights do not establish trained visual quality. Structural/mirror/dependency/
diff checks PASS, 177 runtime modules. No new weights or dependencies downloaded.

## Still blocked

Physical Win32 process-handle verification, sufficient available RAM for model
offload, qualified independent editors, actual trained media quality and target
RTX 2080 Super 10/10 remain pending. No main changes, Actions, paid APIs, merge,
release or interim user build. Next Windows action is the short diagnostic only,
using the new source folder and its SETUP_WINDOWS_QA.cmd, then
RUN_WINDOWS_PROCESS_QA.cmd; send the existing DIAGNOSTICS.zip path.
