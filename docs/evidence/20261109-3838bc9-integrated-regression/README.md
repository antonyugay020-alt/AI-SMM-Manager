# Integrated regression and shared local compute lifecycle

Baseline `3838bc94413a918438aa549d6c99ee2e7cd84e2f`; existing integration branch
and draft PR #4. Implementation is the commit containing this evidence.
Tested runtime fingerprint:
`5e247e039c11904d6a3a9d06de72d1e80bb3d8b78279150093a03095da0cebcc`.
`tested-bytes.json` also attests all CI and root QA helpers, including mask code.

## Windows evidence and scope

The latest available Windows failure summary is the PR #4 comment of
2026-11-09 05:54 UTC and the baseline development checkpoint. It reports the
second real preliminary 3080 Ti run: unprivileged linked-ancestor test failed,
five reviewer calls timed out, one reviewer failed 3/6 semantic controls, and
the first diffusion process was explicitly stopped after a startup stall.
This is not evidence of model-quality rejection for that interrupted image.
The previously attached first-run ZIP remains historical evidence; no new
second-run raw ZIP was available in this Work checkout. Do not invent stack
traces or claim a subsequent physical Windows pass from the summary.

Baseline had already introduced junction fallback, 180s qualification ceiling,
stage/watchdog diagnostics, actual VRAM checks, QA masks and one Desktop root.
Those changes had not received full original-checkout regression. This phase
preserves them and completes their connected contracts and lifecycle behavior.

## Coherent changes

- A shared, bounded, cancellable OS compute lease now coordinates app-owned
  Diffusers, local Ollama text and local vision requests across processes.
  Nested calls on the owning thread are safe. Waiting consumes the request
  time budget; cancellation and parent death release ownership. Local text
  requests use `keep_alive=0`, matching vision, rather than retaining app-owned
  models across the next media workload. No all-model generate/unload sweep,
  external process termination or authority over other programs is added.
  External VRAM occupancy still fails the actual hardware preflight safely.
- Typed runner startup/lock events are validated in order, including monotonic
  timestamps, by the actual subprocess ownership tests. The tests no longer
  mistake new telemetry for an unexpected workload line. Parent EOF death,
  kernel lock release and waiting successor execution remain required.
- The already approved 180s single-call cold-start ceiling has one named
  contract. Two obsolete 45s assertions were corrected. Six-control semantic
  admission, author/reviewer independence, no retry escalation and no paid
  fallback remain unchanged. A timeout remains unavailable, not a semantic PASS.
- All post-inference verification/publication failures retain the bounded
  stage evidence before temporary cleanup, not just failures inside inference.
  Failed native execution includes the actual last stage and a bounded Python
  traceback; device-lock timeout retains its distinct error code. Qt/SQLite
  cancellation, stable task IDs, receipt adoption and recovery remain intact.
- Per-case status reads the actual current project workspace rather than
  another completed project's latest log. VRAM waiting processes Qt events;
  invalid/failed/timeout driver probes persist BLOCKED diagnostic evidence too.
  The existing 3 GiB floor and target 8 GiB envelope were not relaxed.
- QA masks and production inpainting use the same EXIF-oriented geometry.
  Both generated fixtures and valid user-selected binary masks have exact
  source/mask provenance and are copied into diagnostic evidence. A changed
  source or mask blocks execution. Original photographs remain untouched;
  deterministic QA masks do not claim subject segmentation or artistic quality.
- The root mask CI module now establishes its checkout import path explicitly,
  so complete CI also collects from a Python/plugin entry point outside the
  repository. The Windows launcher is normalized under the existing CRLF
  gitattributes policy; checked-out launcher bytes/behavior are unchanged.

## Checks on the final code

Linux / Python 3.12 / Qt offscreen, not Windows or CUDA:

| Check | Result |
| --- | --- |
| All 263 developer modules | 2109 passed + 1048 subtests; zero failures/skips |
| Actual four-shard isolated QA layout (66/66/66/65 modules) | 2109 passed + 1047 subtests; zero failures/skips |
| Complete CI | 326 passed + 77 subtests; 2 native-Windows-only skips |
| Complete CI with forced GC after every case | Same 326 + 77, 2 skips |
| Actual cached tiny Diffusers CPU + Qt image lifecycle | PASS: stop, owner death/child exit, same-ID retry, SQLite reopen, receipt adoption |
| Actual cached tiny AnimateDiff CPU video + original audio | PASS |
| Three runtime/test mirrors, dependency graph, byte manifest | PASS, 163 runtime modules |
| Diff whitespace/integrity | PASS |

The CPU test-only topologies do not certify trained-model quality, inference
performance, GPU memory or cancellation at every CUDA kernel phase. Two skipped
Win32 share-mode/junction checks require execution on Windows. No current live
Ollama qualification was performed in Work; installed 14B discovery/admission
remains part of the real Windows run and is bound to its actual local roster.

Commands (use `/workspace/work/venv/bin/python` for `python` here):

```sh
AI_SMM_TEST_ROOT=/workspace/work/pr4-3838bc9/final-developer QT_QPA_PLATFORM=offscreen python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/pr4-3838bc9/final-regression
AI_SMM_TEST_ROOT=/workspace/work/pr4-3838bc9/ci-final-fixed QT_QPA_PLATFORM=offscreen python -m pytest -q ci --tb=short --junitxml=/workspace/work/pr4-3838bc9/final-ci.xml
python /workspace/work/pr4-3838bc9/run_isolated.py
AI_SMM_TEST_ROOT=/workspace/work/pr4-3838bc9/gc-fixed QT_QPA_PLATFORM=offscreen python /workspace/work/pr4-3838bc9/run_gc.py
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-profiles.json --profile tiny-topology-only --lifecycle
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-video-profiles.json --profile tiny-video-topology-only
python ci/verify_tree.py
git diff --check
```

The two runner scripts, logs, JUnit results and hashes are retained alongside
this checkpoint. Initial baseline CI failed two stdout protocol assertions;
baseline developer regression failed the two outdated timeout assertions. A
first forced-GC attempt found the mask test's root import dependency. None of
those failed attempts counts as PASS; the final complete runs above followed
all corrections.

## Remaining physical acceptance

Update this same integration checkout; run **RUN_WINDOWS_QA.cmd → 1** on
RTX 3080 Ti. The sole user-facing root remains
**%USERPROFILE%\Desktop\AI_SMM_REPORTS** (an existing junction is preserved).
STATUS.json / LATEST.json point to the current session and its DIAGNOSTICS.zip.
Use the installed models and fixtures; there is no automatic download or paid
API. All selected installed reviewers are forcibly qualified again, with exact
UTF-8 evidence and unchanged admission criteria. A new source invalidates old
attestations. Do not ask for, or count, a stale baseline Windows run.

Full Windows Qt/CUDA acceptance remains **BLOCKED / 0 of 10**. After a genuine
3080 Ti preliminary PASS, the same frozen source/model/input identity must
complete ten consecutive clean full target runs on RTX 2080 Super 8 GB; any
failure resets that counter. Work cannot execute those PCs remotely. No main
change, merge, Actions, paid service, model download, user build or release.
