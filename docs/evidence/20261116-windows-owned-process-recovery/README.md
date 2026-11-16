# Windows owned-process cleanup and resource evidence — 2026-11-16

Baseline `83cf60a0dffe980bfbaaa4d99aab081d20720662`; same integration branch and PR #4.
Work began 2026-11-15 UTC; after environment restart, unchanged source/test logs
were verified and actual cached tiny CPU video validation completed on resumption.

## Physical Windows input

The supplied 1,075,751-byte archive is identified by SHA-256 in input-summary.json.
932 source files were checked against the baseline: 924 exact blob matches and
8 CMD files differing only by CRLF conversion. No unrelated source changes found.
No raw user archive, photographs, databases or personal paths are committed.

Actual RTX 3080 Ti / Torch 2.8.0+cu126 native preflight completed. Torch import no
longer hung in this observed run; this is not proof against every possible hang.
Gemma3:4b passed both pixel controls. Basic visible Qt and Ollama/JSON checks PASS.
All 263 developer modules ran: 2066 passed + 1058 subtests, 43 platform skips.
Full Windows CI: 568 passed + 94 subtests, 17 skips, **3 failures**.

- Spool test opened/read the exclusively locked byte via a second Windows
  handle, contrary to mandatory byte-lock semantics. Production locking was
  working. Verify the sentinel after release and the stable inode/size while
  held; existing contention/cancellation checks stay intact.
- Two native log-budget/timeout tests raised the intended error, then temporary
  directory cleanup failed with WinError 32 on child.log. The owner closed a
  kill-on-close Job Object, then waited only for the Popen leader. Termination
  is asynchronous; a Windows venv redirector can exit while its descendant
  still holds the inherited output handle. The source lacked a tree drain.
- Generative image case 0 stopped at SYSTEM_RAM_INSUFFICIENT before pipeline
  loading/inference. The unchanged image floor is 6 GiB available system RAM;
  video uses 10 GiB. The old report did not record actual total/available bytes.
- None of the five text reviewer candidates qualified. Qwen3 scored 5/6 but
  incorrectly asserted that an existing literal was missing; Gemma3 and
  Qwen2.5-coder scored 3/6; Llama failed the response contract and DeepSeek
  returned an incomplete response. UTF-8 request roundtrips and response decoding
  were valid for all five. These are not corrected PASS verdicts.

## Connected implementation

WindowsJob now explicitly terminates only its owned job and retains the handle
while polling ActiveProcesses to zero with a finite timeout. Shared native /
FFmpeg cleanup drains the tree before closing the job, reaps the leader and
closes stdin even on drain failure. The QA supervisor uses the same drain.
KILL_ON_JOB_CLOSE still protects owner death; unrelated processes are untouched.
A drain timeout fails closed; it is not hidden by retrying file deletion.

Generation logs retain RAM available/total/required bytes at the existing gate.
HOST and per-second VRAM samples also contain system-memory measurements.
Resource floors, offload strategy and quality gates are unchanged. Qualification
now retains terminal response diagnostics supplied by the transport, even when
completion validation raises before the response reaches the reviewer. Partial
responses are not decoded or accepted and no extra request is sent.

The three runtime mirrors remain identical. New tests cover job drain ordering,
invalid deadlines, surviving-child timeout, cleanup failure with mandatory handle
closure, an actual nested Python child inheriting its owner's output, exact RAM
boundary/rejection evidence and one-call incomplete-reviewer rejection.

## Executed on final Linux source

```
QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python -m pytest -q ci/test_windows_tree_drain.py ci/test_windows_resource_evidence.py ci/test_media_spool_lease.py ci/test_native_log_budget.py ci/test_owned_process_tree.py ci/test_windows_qa_source.py --tb=short
AI_SMM_TEST_ROOT=/workspace/work/windows-qa-20261115/dev-data QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/windows-qa-20261115/developer
AI_SMM_TEST_ROOT=/workspace/work/windows-qa-20261115/ci-data QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python -m pytest -q ci --tb=short --junitxml=/workspace/work/windows-qa-20261115/final-ci.xml
AI_SMM_TEST_ROOT=/workspace/work/windows-qa-20261115/gc-data QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python /workspace/work/windows-qa-20261115/run_gc.py
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 /workspace/work/venv/bin/python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-profiles.json --profile tiny-topology-only --lifecycle
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 /workspace/work/venv/bin/python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-video-profiles.json --profile tiny-video-topology-only
/workspace/work/venv/bin/python ci/verify_tree.py --output /workspace/work/windows-qa-20261115/tree.json
git diff --check
```

- Focused suite: 51 passed + 9 subtests. Initial fixture omitted an exception
  constructor's stage argument; corrected before final full regression.
- All 263 developer modules: 2109 passed + 1062 subtests, no failures/skips.
- Full CI and forced-GC CI: each 599 passed + 94 subtests; 2 Windows-only skips.
- Actual tiny CPU Qt/Diffusers image and video generation PASS. Image lifecycle
  includes cancellation, owner death, same-ID retry, SQLite reopen and committed
  receipt recovery. These test-only models do not prove trained semantic quality.
- Structural/mirror/dependency checks PASS, 177 modules; tested bytes recorded.

## Remaining boundaries

The new Win32 drain still requires physical Windows verification; Linux tests
exercise the API protocol using doubles plus actual POSIX process groups.
No new Windows PASS is claimed. The full Windows run remains BLOCKED on
reviewer admission and RAM, and final target acceptance stays **0/10**.
No Actions, paid API, downloads, main change, merge or release were performed.

Next physical test uses the same RUN_WINDOWS_QA.cmd -> 1 on the updated source;
existing QA .venv may be selected explicitly with AI_SMM_QA_PYTHON rather than
reinstalling it. Existing model profiles/settings remain in AI_SMM_REPORTS.
This run should verify the Windows fixes and collect resource evidence; it cannot
be presumed to fix the failed reviewer semantics or insufficient RAM.
