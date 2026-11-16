# Two-machine Windows QA source checkpoint

Baseline `c49fbd0a867e71ce6123f47e620a3a5ed38f00fd`.
Tested implementation `7fb73b59553fc7492cc866662e19bdceb77910e6` on the existing PR #4 integration branch.
No Windows access, release/build, merge, model downloads, paid API or Actions.

## Implemented

One-launch `RUN_WINDOWS_QA.cmd` and `windows_qa_session.py`: one-time installed
profile/plan/fixture/vision setup, persisted settings, early host/prerequisite
reports, distinct actual RTX 3080 Ti preliminary and RTX 2080 Super target roles.
Each run copies the frozen source cleanly and isolates data. One preliminary
full run exports an input/source-bound attestation; target requires it and runs
10 complete passes. Failure, replay, source/input/model/profile/dependency drift
or a live descendant resets the target count. Source QA cannot authorize release
or bypass the existing packaged-candidate supervisor.

Full existing text/Qt/independent review + developer regression + CI + all four
trained CUDA media engines + native output metrics and qualified local vision
review are mandatory. Video/reference/audio, mask preservation, VRAM allocated/
reserved and whole-card samples, screenshots, SQLite reopening, real child stop,
owner crash/EOF shutdown, retry with stable ID and adoption without re-inference
are recorded. Owned Win32 Job Object is assigned before a suspended child runs;
cleanup does not target unrelated process names. Local-only transport checks,
credential isolation and disabled outbound proxy routes exclude paid services.
No download/pull/install operation is in the QA launch path.

Complete diagnostics stay under `%LOCALAPPDATA%\AI_SMM_Manager\QA`, including
early Python/bootstrap failure, source snapshots, raw logs/JUnit, all QA data/
exports retained before temporary cleanup, metrics/model observations, GPU
samples, hashes and status. Reports are local; nothing is uploaded automatically.
[Exact user steps and scope](../../WINDOWS_ONE_CLICK_QA.md).

## Exact Work verification

Linux / Python 3.12 / PySide6 6.12.0. Main interpreter
`/workspace/work/venv/bin/python`; existing cached tiny neural fixture uses
`/workspace/work/generative/venv/bin/python`. No new model download.
`AI_SMM_TEST_ROOT` points under `/workspace/work/qa-next`;
`QT_QPA_PLATFORM=offscreen` applies only to these Linux checks.

- `python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/qa-next/frozen-regression`:
  **2109 tests + 1046 subtests PASS**, all 263 modules, JUnit 3155, zero errors,
  failures or skips. Verified runtime fingerprint below matches the final source.
- `python -m pytest -q ci --junitxml=/workspace/work/qa-next/confirmed-ci.xml`:
  **292 + 77 subtests PASS**. Includes synthetic counter/report controls, GPU
  role separation, stale preliminary rejection, provider denial, no-Windows
  refusal with complete early reports, setup wizard mapping, semantic uncertainty,
  blank/no-op/mask leakage rejection, real audio comparison and mocked Win32
  ownership protocol. Mocked Win32 calls are not actual Windows API evidence.
- Same complete CI with `gc.collect()` after every case: **292 + 77 PASS**.
- `OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-profiles.json --profile tiny-topology-only --lifecycle`:
  **PASS**, actual existing Studio panel, real separate tiny Diffusers CPU
  inference, subprocess stop, killed owner, actual child EOF exit, same-ID retry,
  independent SQLite open/integrity/hash checks and committed-result adoption.
  The instruction topology is a test model, not trained quality evidence.
- `python AI_SMM_Work_Architecture_A75/windows_acceptance.py --gui-child`
  with isolated root/local-only flags: Linux/offscreen Qt launch/navigation,
  two-project isolation, real PDF and shutdown **PASS**. Not a Windows run.
- `python ci/verify_tree.py`: **PASS**, 161 mirrored runtime modules, 785 source
  entries, fingerprint `9f1728b84385dd5be1b193c47aaa0f5c5907325218c84e042e4e65eb91bccdee`. `git diff --check` PASS.

## Failed iterations and architectural corrections

1. New process telemetry exposed an old process fixture without a PID. Updated
   the fixture to the actual Popen contract. More importantly, metadata writes
   now occur atomically inside the child-owning try/finally: a failed diagnostic
   write cannot abandon a launched model process.
2. The new native audio fixture used `.5`, rejected by this FFmpeg build.
   Corrected it to `0.5`; real unchanged audio passes and replaced audio fails.
3. Audit found that main local-only QA flags/real model profiles would pollute
   isolated mocked contract tests. Stripped them only from offline test children;
   live QA remains strictly restricted. Approved local model inventory excludes
   cloud/embedding entries instead of accidentally dispatching them.
4. Old acceptance assumed a packaged candidate, only target GPU and per-case
   operator review; old ZIP collection dropped nested artifacts. Added a
   separately labelled non-release source mode, both actual GPU roles, mandatory
   qualified local quality review and recursive/retained diagnostics, without
   inventing a packaging PASS or changing the release supervisor requirements.
5. Bound source/input/model/dependency evidence across machines and all target
   passes; added allocated/reserved peaks, 8 GB envelope checks and whole-card
   monitoring. Failed/uncertain quality or missing memory evidence cannot pass.

## Remaining factual limits / next action

No actual Windows, CUDA GPU, nvidia-smi/Job Object execution, trained production
media models or real local vision judgement was run in Work. Local vision
controls/metrics are not a guarantee of perfect artistic/factual judgement;
all artifacts/observations remain inspectable. Audio metrics sample up to 2 s;
video reviews sample frames. Cancellation/crash tests occur at child startup,
not every possible GPU kernel phase. These limits are explicit in source/reports.

**RTX 3080 Ti preliminary 0; RTX 2080 Super target Windows 0/10.**
Next: user launches menu 1 on RTX 3080 Ti, resolves BLOCKED prerequisites without
unapproved downloads, returns its diagnostics for actual fixes; then same frozen
source/inputs/models and menu 2 on RTX 2080 Super. No direct Work access is needed.
Any error requires fixing and restarting a clean cycle. Do not call this a
finished program or final release acceptance based on Linux evidence.

## Final diagnostic hardening

Final tested source `36f5decbec04918d51d3f237169515f24fdc2049` follows the first implementation above.
Native tool failures now retain bounded stderr/exit code in SQLite, including
non-generative jobs. FFmpeg libx264/AAC and actual NVIDIA memory samples are
checked before lengthy model tests. N/A/impossible/empty memory samples cannot
count. Contradictory FAIL/UNCERTAIN model fields cannot be overridden by a
summary PASS. No download, paid call, build, merge or Actions run.

Final exact-source verification: full developer regression **2109 + 1046 PASS**,
all 263 modules / JUnit 3155; complete CI **294 + 77 PASS**, also forced-GC;
actual tiny CPU Qt cancellation/crash/retry/reopen/adoption **PASS**; actual
Linux/offscreen Qt child/navigation/project/PDF **PASS**. Updated logs and
manifest are prefixed `final-`. Final runtime fingerprint `d060bce030f0ad78f7e6333718d91039db789b1d2561c2f7a1ecf0c59ad3d283`.
Two new native diagnostics/memory validity checks extend the initial 292 CI
checks. Native Win32, GPUs, real vision model and Windows E2E remain unrun.
Preliminary **0**, target **0/10**. The prepared source launch is not a release.
