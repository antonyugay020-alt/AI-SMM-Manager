# Current integrated development state — 2026-11-07

Development checkpoint, not a release or acceptance claim.

## Source lineage

- PR #4 stays draft on `integration/ai-smm-pr2-20261106`.
- Observed PR head before work: `83ba4545404b0af8a62ebca4228764a784c769e8`.
- Incorporated `codex/pr4-media-atomic-recovery-20261107` at
  `77443037c5da13a486dd6f0f1b58a3812315afe3` by fast-forward, after verifying
  the old integration HEAD is its ancestor. All eight descendant commits
  remain intact. Local changes were saved as a patch/stash before advancing.
- Implemented and tested source commit: `2a11d3f6d1a7579d06c777120a0f89500e67c273`.
- Observed main: `0c3b42f714a63afd3b2308136753e4d0dabacd10`; its three newer
  review-infrastructure commits were inspected, not copied or merged.
- Resulting evidence commit is the commit containing this checkpoint. Its
  parent is the tested implementation commit. Neither main nor PR #2 changed.

## Integrated vertical slice

- Native Kie file-stream upload and Market task-status adapters, implemented
  from official contracts checked on this date:
  https://docs.kie.ai/file-upload-api/upload-file-stream and
  https://docs.kie.ai/market/common/get-task-detail .
  Multipart streams are bounded, use random remote filenames, require separate
  upload consent, and validate the returned byte count and public HTTPS URL.
- Authenticated requests refuse redirects; provider bodies/credentials stay
  out of error messages. JSON replies are capped. No automatic paid retries.
- `PaidMediaComposition.native_gateway` binds native upload, createTask and
  status to the same durable ledger displayed by Qt. Verified maximum quotes
  and one-use approvals remain mandatory; generation is disabled by default.
- Actual Studio has an explicit “Получить результат Kie.ai” action. Its idle
  worker is created only for a confirmed/completed project-owned task; no
  startup provider traffic. It opens/closes its own SQLite connection inside
  QThread and freezes project/workspace before launch. Main-window shutdown
  waits for native completion. Switching projects cannot retarget results or
  show a prior project's completion message in the new project.
- Image/video bytes pass decoding/ffprobe, ingest outbox publication and SHA-256
  receipts. Reopening a completed task validates local bytes without polling.
- Recovery during an active paid submission can now retain a subsequently
  confirmed task ID. Duplicate provider IDs fail uncertain without freeing
  the reservation. Downloads have per-attempt staging names, so an interrupted
  or competing attempt cannot block recovery or delete another attempt's file.
  Concurrent completion reconciles the one durable publication instead of
  failing on its identity collision. Truncated Content-Length is rejected.
- Durable cost-gate methods now close their SQLite handles on success and
  failure; SQLite transaction contexts alone did not close them. No provider
  billing is invented or automatically settled from a task ID.

## Exact verification

Linux, Python 3.12, real PySide6/Pillow/FFmpeg, provider HTTP fixtures only.
Environment for all app/Qt checks:
`AI_SMM_TEST_ROOT=/workspace/work/test-app-root QT_QPA_PLATFORM=offscreen`.
Python executable: `/workspace/work/venv/bin/python`.

- `python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/regression-final`:
  PASS, all 262 developer test modules, **2109 passed + 1024 subtests**;
  JUnit counts 3133, zero failures/errors/skips. Source fingerprint unchanged
  before/after: `0e0d4d1a1a28c14bec8752ec9e9c70a7cce1716a62a28fbb1e75ca2782e2f515`.
- `python -m pytest -q ci --junitxml=/workspace/work/final-ci.xml`:
  PASS, **178 passed + 77 subtests**. Includes native multipart/HTTP contracts,
  real shared ledger, duplicate remote IDs, recovery during dispatch, actual
  QThreads, concurrent completion, project switch during blocked provider read,
  safe errors and real MP4 verification/publication. Provider calls are fixtures.
- `python ci/native_media_processing_acceptance.py`: PASS for actual local
  photo editing, owner publication, FFmpeg video trimming, codec/duration and
  unchanged original; no remote generation or Windows certification.
- Actual `Studio()` offscreen smoke: PASS, live widget construction, same
  ledger instance, result-worker binding, native worker drain and clean close.
- `python ci/verify_tree.py`: PASS, all three runtime mirrors byte-identical,
  139 source modules, 717 fingerprinted source/test assets.
- `git diff --check`: PASS. No package, ZIP or intermediate build produced.

Evidence and complete per-file source hashes:
[evidence/20261107-native-kie-qt](evidence/20261107-native-kie-qt).
Earlier failed attempts are recorded in `verification.json`; they are not PASS.
A prior all-tests collection failed on the default nonwritable Documents root;
rerunning with the application's existing test-root setting fixed environment
setup. Two new Qt fixtures initially used invalid nonhex project IDs; corrected
fixtures now test the real project identity contract.

## Remaining defects and acceptance blockers

- Windows clean-unpack real-model GUI E2E: **NOT RUN, 0/10**. Linux offscreen
  tests cannot advance that counter or the older full-delivery gates in nested
  AGENTS.md. No final/NOT_RELEASE archive is authorized by these results.
- Live Kie Nano Banana/Kling generation, real-model quality/independence and
  final billed costs: NOT VERIFIED. No configured secret and no authorization
  to spend. Contract fixtures are not proof of live provider success.
- Verified provider tariff/quote source and GUI paid-generation confirmation
  are not yet connected. GUI currently creates offline image drafts and
  retrieves already confirmed results; it does not launch paid generation.
- Ollama executable/service unavailable here (`localhost:11434` refused).
  Local model execution and target RTX 2080 Super 8GB performance unverified.
- The paid text-provider path still needs integration with the same durable
  cost authority; media ledger coverage must not be represented as coverage
  of every paid AI call. No live text API calls were made in this session.
- Whole-dialogue AI routing to media creation/editing, video/reference controls,
  user cancellation and every app-level recovery path still need integrated
  real-model GUI acceptance. This checkpoint does not certify them.
- Independent cloud model review of this new source: NOT RUN, no paid approval.
- No Actions dispatched. The existing active `independent-review.yml` runs
  on `pull_request_target`, ignores `[skip ci]`, and calls paid APIs. It was
  explicitly disabled via GitHub API before pushing (verified
  `disabled_manually`). Other automatic workflows are skipped by commit
  messages. Re-enable paid review only after explicit user authorization;
  missing independent review continues to block release.

## Next coherent milestone

Integrate paid text dispatch with the shared durable gate and project/task-scoped
approval; add restart and ambiguous-charge tests before connecting GUI paid
image/video generation to a verified maximum quote. Keep local Ollama-first
routing operational offline, then connect image/video/reference selection and
cancellation to the durable task state machine. Only after appropriate provider
and Windows execution are available run real-model whole-app acceptance,
independent review, and ten consecutive clean Windows E2E runs on final bytes.
Do not repeat old source-only PASS as acceptance. Do not merge or release.
