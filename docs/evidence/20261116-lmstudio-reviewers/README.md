# Local text integration — 2026-11-16

Baseline: `bc067dbe810013577150c7ce5358775746840d78`, existing PR #4,
`integration/ai-smm-pr2-20261106`. The commit containing this checkpoint is the
tested source continuation; no main/merge/Actions/paid calls/build/release.

The incoming physical process diagnostic on the baseline passed 26 tests and
9 subtests, with no failures/skips. The three previously failing Windows
process/file ownership scenarios passed. Its archive/source hashes and scope
are retained in `prior-windows-process-result.json`. It does not establish full
Windows Qt/CUDA acceptance; target remains 0/10.

## Implemented and checked

- Explicit LM Studio model route through the existing text gateway, local
  compute lease, cancellation, output/schema limits and UTF-8 diagnostics.
  Exact loaded model identity is checked; no download/load/unload calls,
  inherited proxy, redirect, credentials or fallback to another provider.
- Shared application/reviewer/full-QA discovery includes the configured local
  model. Qwen variants and common Llama/Gemma transport aliases do not add
  spurious independent families. Six-control oracle and minimum independence
  remain unchanged. Cache/child certificates bind local route and QA weight hash.
- Optional persisted QA configuration selects an actual loaded server ID and
  existing GGUF file, retains media configuration, hashes the file and checks
  roster drift. The file hash is user-selected-file evidence, not server-RAM
  weight attestation. Loaded context, GPU offload and actual model quality must
  be measured on Windows.
- `RUN_WINDOWS_REVIEWER_QA.cmd` isolates the current editor-capacity blocker in
  the existing single diagnostic folder. It has a bounded owned child process,
  preserves raw qualification evidence, and cannot advance acceptance counters.

Initial full regression found a new static import cycle caused by acquiring
UTF-8 evidence from the upper transport layer inside the low-level adapter.
The final adapter receives an evidence object explicitly. Final full regression
passes the unchanged import-graph guard. Other review fixes: consistent backend
default ports, inventory consuming the existing call deadline, closure of HTTP
error bodies, and no stale admission after changing a local endpoint/weight hash.

## Final verification

Linux Python 3.12; runtime fingerprint
`19dcb6f4b10b843ddbb149522e1e25faf8aa622f64090b75022669f7e5ad9f59`.
`tested-source.json` additionally binds root launchers, all CI tests and source
QA controller bytes; `tree.json` verifies all three runtime mirrors.

| Check | Actual result |
|---|---|
| All 263 developer modules | 2109 passed + 1062 subtests; no failures/skips |
| Full CI | 619 passed + 94 subtests, 2 native-Windows skips |
| Full CI with GC after every test | 619 passed + 94 subtests, 2 native-Windows skips |
| Focused transport / Windows QA contracts | 50 passed |
| Structure / three runtime mirrors | 177 modules, PASS |
| Actual cached tiny CPU image + lifecycle | PASS; cancellation, owner death, same-ID retry, receipt adoption, SQLite reopen |
| Actual cached tiny CPU video-to-video | PASS, 4 frames; semantic quality not accepted |
| Actual offscreen Qt dialogue/media | PASS; semantic model response is a test fixture |
| Same Qt path, cancellation + corrupt optional paid ledger | PASS; zero paid calls |

Commands, run from repository root with the prepared Python:

```sh
AI_SMM_TEST_ROOT=<isolated-dev-root> QT_QPA_PLATFORM=offscreen python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output <evidence-dir>
AI_SMM_TEST_ROOT=<isolated-ci-root> QT_QPA_PLATFORM=offscreen python -m pytest -q ci --tb=short --junitxml=<evidence-dir>/ci.xml
python ci/verify_tree.py --output <evidence-dir>/tree.json
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-profiles.json --profile tiny-topology-only --lifecycle
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-video-profiles.json --profile tiny-video-topology-only
QT_QPA_PLATFORM=offscreen python ci/studio_media_dialogue_smoke.py
QT_QPA_PLATFORM=offscreen python ci/studio_media_dialogue_smoke.py --cancel --corrupt-paid-ledger
git diff --check
```

Forced-GC CI uses a `pytest_runtest_teardown` hookwrapper calling `gc.collect()`
after each test. Logs/XML accompany this checkpoint. The loopback HTTP tests
exercise a real local socket with injected responses; they do not run Qwen or
prove model quality. Tiny CPU Diffusers models verify execution/lifecycle only.

## Remaining physical blockers

The earlier full Windows run had zero qualified independent text reviewers and
5.23 GiB available RAM at image loading (unchanged floor 6 GiB). Video requires
10 GiB; the later short process diagnostic observed 6.68 GiB. Neither issue is
declared solved by adding a provider. LM Studio/Qwen2.5 14B live qualification,
resident-memory effects, Windows Qt/CUDA and 10 clean RTX 2080 Super runs remain
unverified. No automatic model eviction or resource/quality threshold change.

The next concrete physical step is the focused reviewer diagnostic described in
[LOCAL_MODEL_RUNTIME.md](../../LOCAL_MODEL_RUNTIME.md), using installed weights.
It writes the same `AI_SMM_REPORTS/DIAGNOSTICS.zip`; full Windows media acceptance
follows only after resource availability and independent editor capacity permit it.
