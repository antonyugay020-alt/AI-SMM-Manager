# Integration checkpoint — project-scoped media recovery

Baseline branch: `codex/pr4-media-atomic-recovery-20261107`, commit `6375a75d6f61f621b140eda4495345703f464882`.
Scope: media ingestion/replay/GC project-identity fencing, correct per-project
pending counts, and cross-project regression cases. No provider calls, no
billing, no Windows runner dispatch.

## Verified from GitHub source (2026-11-07)
- Source code is mirrored in three production/developer/test runtime paths.
- Regression tests are included in the existing developer test inventory.
- Prior PR #4 head `83ba4545` is not accepted for release. Earlier matrix
  success applies only to its recorded exact SHA, not this checkpoint.
- PR #2 is Draft with conflicts; PR #4 remains Draft. Both main and PR heads
  are intentionally unchanged because the trusted main reviewer workflow
  still auto-triggers paid-capable `pull_request_target` jobs on PR updates.
- Recent independent reviewer job `42363708365` failed before any runner
  steps; no reviewer verdict or artifact is available for the current head.

## Validation
- GitHub Git-tree SHA and mirror equality: verify after committing.
- Offline Python/SQLite tests: NOT RUN (private repository checkout is not
  available in the local execution container; GitHub Actions runners are
  unavailable). The new regression cases are **not** counted as PASS.
- Interactive Windows Qt GUI E2E with real models: 0/10 verified.
- Independent model review of this source: not accepted.

## Next integrated milestone
Unify media recovery and paid-execution cost ledgers with the existing
staged cost-authorization work, preserving the Qt budget UI contract.
Before updating a PR, disable automatic paid-capable review on the trusted
main workflow or obtain explicit approved review budget; then run exact-SHA
Linux/Windows regression shards, aggregate evidence, independent review
and full Windows GUI acceptance. Do not merge or release.
