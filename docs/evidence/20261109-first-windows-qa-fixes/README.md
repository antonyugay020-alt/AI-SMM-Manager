# First real Windows QA: linked fixes and available regression

Baseline: `8f48fadb7de51402edcc91bcaed68d30ef2b311d`, existing integration branch / PR #4.
Resulting implementation: the commit containing this checkpoint; tested runtime
fingerprint `8ac7247af0f18cc0ad986b8b58e3ac25545b97ab4154bf7494de215f7118949c`.
`tested-bytes.json` binds runtime/test mirrors and additional CI source bytes.
All execution reported below occurred on Linux, Python 3.12, Qt offscreen.
Actual Windows/CUDA fixes remain subject to the next physical Windows run.

## Evidence and common causes

Input was the user's first real RTX 3080 Ti Windows QA DIAGNOSTICS.zip, treated
only as diagnostic data. Raw user SQLite, photos, exports and machine paths are
not republished here. Actual Windows Qt smoke and basic Ollama contracts passed;
full acceptance did not. In particular, one missing CI import prevented an
entire 66-module shard from being collected. This was never a clean full run.

1. Media staging compared the same five fields from path `stat` and descriptor
   `fstat`. In the actual host's CPython 3.12.10, these have different `ctime`
   semantics: `win32_xstat` explicitly copies birthtime into ctime; the handle
   path `_Py_fstat_noraise` preserves FILE_BASIC_INFO.ChangeTime. Both already
   use native Windows APIs; this is **not** merely a CRT/inode-zero assumption.
   References: [path implementation](https://github.com/python/cpython/blob/v3.12.10/Modules/posixmodule.c#L2139),
   [handle implementation](https://github.com/python/cpython/blob/v3.12.10/Python/fileutils.c#L1233),
   [metadata conversion](https://github.com/python/cpython/blob/v3.12.10/Python/fileutils.c#L1109).
   The shared source boundary now uses one Win32 metadata representation:
   64-bit volume identity, full 128-bit file ID, size, write and change times.
   Native read-only handles deny writers/deletion, open reparse points without
   following them, reject all reparse attributes and verify the opened final
   path. Ancestor link checks and post-copy identity checks remain. A second
   read/hash also rejects content changes invisible to metadata (e.g. mappings).
   This common boundary caused 15 of the 18 CI failures, including downstream
   GUI publication/cancel/retry/recovery failures; task identity, durable
   receipts and cancellation fences were retained rather than bypassed.
2. Two CI tests leaked SQLite handles: unittest cleanup ran after their inner
   TemporaryDirectory had already tried deleting Windows-locked files, and
   the connection transaction context was mistaken for connection ownership.
   Explicit closing fixes both. The adjacent real-model crash-recovery QA also
   now closes its independent inspection connection and reads JSON as UTF-8.
3. Isolated developer copies omitted the checkout-relative `ci` dependency
   tree. QA now copies the complete tree; its full inventory floor is 263.
   A symbolic-link test now declares missing Windows privilege (1314) as a
   capability skip only. Separate real Win32 share-mode and unprivileged
   directory-junction tests run on Windows; portable reparse/race/hash tests
   remain mandatory everywhere. No production link protection was removed.
4. On an unavailable source review, a caller-specified panel was discarded
   and automatic roster qualification could make six additional model calls.
   Explicit panel ownership now prevents that rediscovery. The original
   `<=12` assertion is unchanged; the reproduction now makes **5** fixture
   calls, remains `write_blocked`, and never rewrites from an unavailable audit.
5. Source QA snapshot filtering omitted `.md`, dropping required AGENTS.md /
   PROJECT_VISION.md before the real packaging guard. The snapshot now preserves
   these files and root authorities. The guard itself is unchanged; its actual
   clean-candidate temporary packaging/unpack contract passes in full CI.
6. Archived model reply bytes decode correctly as UTF-8. Replaying all six
   archived replies and original seeds preserves all six FAIL verdicts (3/6,
   3/6, 4/6, then three invalid contracts); these are not new live qualifications.
   No evidence supports silently repairing the Cyrillic or promoting verdicts.
   Transport now explicitly declares UTF-8, strictly decodes response JSON,
   and synthetic qualification retains exact prepared wire request UTF-8,
   request/response byte lengths and SHA-256, plus input/reply evidence.
   These diagnostics are excluded from bounded child environment certificates.
   Qualification criteria, six controls and one-call limit remain unchanged.

## Final available checks

- All **263 developer modules**: **2109 passed + 1047 subtests**, no failures.
- Exact QA isolation layout, actual four subprocess shards **66/66/66/65**:
  **2109 passed + 1046 subtests**, no missing modules/failures/skips. This
  additional Linux execution specifically exercises the formerly missing CI
  imports in the same layout and runner used by Windows QA.
- Entire CI: **303 passed + 77 subtests; 2 skipped**. The two skips require
  actual Win32 APIs/junction creation; they are not Windows PASS claims.
  Entire CI with forced garbage collection after each case: same result.
- Actual Qt + separate cached tiny Diffusers CPU inference: image publication,
  stop, owner crash/child exit, same-ID retry, SQLite reopen and committed
  receipt adoption PASS. Actual tiny video V2V with original audio PASS.
  Tiny models do not establish trained-model quality, VRAM or CUDA acceptance.
- Source/test three-way mirrors (162 runtime modules), dependency graph,
  integrity and `git diff --check` PASS. No release guard was weakened.

Commands (Python is `/workspace/work/venv/bin/python`; writable test roots must
be supplied in this Work environment):

```sh
AI_SMM_TEST_ROOT=/workspace/work/windows-first-qa/developer-final-data QT_QPA_PLATFORM=offscreen python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/windows-first-qa/final-code-regression
AI_SMM_TEST_ROOT=/workspace/work/windows-first-qa/final-runtime QT_QPA_PLATFORM=offscreen python -m pytest -q ci --junitxml=/workspace/work/windows-first-qa/ci-final.xml
python /tmp/run_windows_isolation_final.py
AI_SMM_TEST_ROOT=/workspace/work/windows-first-qa/gc-runtime QT_QPA_PLATFORM=offscreen python /tmp/run_ci_gc.py
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-profiles.json --profile tiny-topology-only --lifecycle
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-video-profiles.json --profile tiny-video-topology-only
python ci/verify_tree.py
git diff --check
```

The two `/tmp` runner scripts are preserved beside this document for audit.
An initial CI invocation without AI_SMM_TEST_ROOT failed 7 app-import cases on
Work's read-only home directory; that invocation is not counted as PASS. Proper
explicit isolated roots resolved the environment error. Subsequent final runs
above use the frozen tested source. Earlier partial runs are not acceptance.

## Next exact step / remaining blockers

Update the existing source checkout, launch **RUN_WINDOWS_QA.cmd**, choose **1**
on RTX 3080 Ti. All six installed local models are forcibly requalified by the
same full run; new UTF-8 evidence is retained even when qualification fails.
The report root remains `%LOCALAPPDATA%\AI_SMM_Manager\QA`; STATUS.json and
LATEST.json locate the new session and per-pass DIAGNOSTICS.zip. Existing logs
are retained; changed source invalidates old attestations/counters.

Work cannot execute real Win32 APIs, NVIDIA CUDA/VRAM or the user's installed
Ollama models. No new live qualification or Windows PASS is claimed. Six
archived reviewer FAILs remain a genuine admission blocker unless those models
pass a fresh full qualification; the criteria have not been lowered. Windows
clean target acceptance remains **0/10**; 3080 Ti preliminary does not advance
2080 Super acceptance. No model download, paid API, Actions run, merge,
intermediate user archive/build or release was performed.
