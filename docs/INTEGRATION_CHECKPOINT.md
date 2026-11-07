# Controlled integration checkpoint — real Qt paid-media composition and recovery

Protocol: `docs/CONTROLLED_INTEGRATION.md` (2026-11-07).
Baseline branch HEAD: `657478f94c423ae269dfe373e66f2d8e7b40489d`.
Integrated source commit: `ab89dd7ef6d08be0a0f99aefc62bedd0cbea1ea1`.
Resulting checkpoint commit: the current HEAD of
`codex/pr4-media-atomic-recovery-20261107` containing this document; verify against GitHub ref before
resuming. This checkpoint is development evidence, **not** acceptance.

## Completed coherent milestone
- Added `PaidMediaComposition` to all three mirrored source trees.
  It owns one stable global SQLite cost ledger, exposes it to Qt, and
  requires explicitly supplied verified provider adapters/quotes before
  constructing a Kie gateway. No provider endpoint or tariff is invented.
- The actual `Studio` Qt composition root now instantiates this shared
  ledger at `ROOT/paid_cost_ledger.sqlite3` and passes that exact gate to
  the Kie queue. An unavailable/corrupt paid ledger leaves offline project
  work available but no paid gate is exposed.
- The queue marks interrupted `submitting` operations as
  `submission_unknown` on refresh, scoped to the selected project.
  It never retries a potentially billable request or polls the provider.
- Budget controls are disabled on ledger read failure instead of
  advertising an editable but unverified spending limit.
- Added offline composition regression scenarios (shared ledger after
  restart, no paid call without consent, interrupted-send fencing, provider
  adapter validation) and Qt scenarios (recovery, ledger failure, real
  budget binding).

## Verification and defect inventory
- GitHub ref and source commit verified: `ab89dd7ef6d08be0a0f99aefc62bedd0cbea1ea1`.
- GitHub tree/compare verified 11 changed paths. All three mirrors of
  `app.py`, `kie_media_queue_ui.py` and `application_paid_media.py`
  have identical respective blob SHAs:
  `6e0bc0010245800ca7c40d1abb33d00bb43cb723`,
  `b8634ed2d4274f9349bc6c59afab19d58554e4d1`,
  `518c514c1ce667a0578f1e165df8d3d9f17598fb`.
- Local Python 3 syntax-only check of the newly drafted composition
  module and its regression file: `python -m py_compile` PASS in the
  isolated working container. This is not a GitHub checkout or runtime test.
- Exact integrated test commands **NOT RUN**:
  `python -m unittest ci.test_application_paid_media_composition`;
  `python -m unittest ci.test_kie_media_queue_qt`;
  `python -m unittest discover -s ci -p 'test_*.py'`.
  The private GitHub checkout is unavailable to the local execution
  container; PySide6 is also absent there. No new unit, Linux/Windows
  regression or GUI test is claimed PASS.
- Latest examined cross-platform run `42340590901`: 4 Linux shards,
  4 Windows shards, offline reviewer and aggregate gate all failed before
  executing steps; no regression artifacts. No root-cause attribution to
  application code is possible from these empty jobs.
- Codex Qt integration run `42195746937` succeeded and has its diagnostic
  artifact, but is not acceptance for this SHA.
- Latest examined independent cloud reviewer `42363708365`: failure
  before steps, no artifact. Independent review of this HEAD: **not accepted**.
- Clean-unpack, real-model Windows Qt E2E: **0/10**.
- PR #2 (`5b3bae111100b86420b520527b9a5755e04b85ef`),
  PR #4 (`83ba4545404b0af8a62ebca4228764a784c769e8`) and main
  were not changed. No paid CI or provider API was invoked.

## Next coherent milestone
- Execute exact-SHA offline composition, queue Qt, AI-task, project
  isolation and recovery tests on a full checkout; address all confirmed
  root causes as a single integrated package, not piecemeal.
- Verify the actual Kie upload/status contracts and provider quote source;
  wire only verified adapters with explicit user approval and the same
  durable ledger. Keep the Qt queue offline until this is proven.
- After authorized CI capacity is available: full Linux/Windows regression
  shards + aggregate, independent review, and 10 genuine clean-unpack
  Windows Qt E2E PASS before any release. No merge into main.
