# AI SMM Manager — P0 full architectural integration request

**Objective:** Deliver one coherent, tested architecture repair for the Windows PySide6 AI SMM Manager, NOT a one-bug-at-a-time series of patches. Stay within this private repository and create a draft pull request. Do not modify main directly.

Authoritative sources: `AI_SMM_Work_Architecture_A75/PROJECT_VISION.md`, `AGENTS.md`, `RELEASE_POLICY.md`, issue #1, existing regression/acceptance tests, and the Windows CI results.

## Verified baseline
Cross-platform CI (run 42189115125): all four Linux test shards passed; all four Windows test shards failed. The Windows failures are not proof of one issue. They exhibit shared causes plus business contract failures:
- UnicodeDecodeError / UnicodeEncodeError for Russian UTF-8 text caused by locale-based `cp1252` decoding/encoding. Enforce explicit UTF-8 consistently in production and testing boundaries; ensure tests can run across Windows versions/locales without weakening intended assertions.
- `WinError 32` preventing deletion/replacement of `project.db`: fix SQLite connection and file-handle lifetime with correct atomic transaction, rollback, crash recovery and resource disposal semantics.
- Batch publication replay/identity incorrectly accepted: `test_batch_result_identity_cannot_replay_or_share_old_artifact` requires ValueError and did not get it.
- Revision checkpoint identity/isolation: `test_revision_checkpoints_are_isolated_and_seed_posts_are_not_approved` raised KeyError with a Russian-derived key.
- Two Windows test shards aborted at collection due to encoding before test execution. Preserve failure evidence and diagnose the entire graph.

## Scope (systemic, not isolated)
Audit all related contracts: ApplicationRuntime, Qt workers/lifecycle, model invocation, task orchestration, project identity and isolation, document generation, publication, SQLite, file effects/outbox, recovery, AI independent review, and release gates.
Design and implement coherent changes across affected modules, synced to developer mirror. Include regression tests for confirmed root causes; do not delete tests or disable assertions.
Only propose source/test changes. Keep CI workflows, release policy, QA acceptance and security controls unchanged.

## Acceptance
1. Existing architecture and Python tests on Linux + Windows, all eight CI shards PASS with no critical skipped failures.
2. Independent review of proposed changes; use code review evidence, not invented model judgments.
3. Actual interactive Windows Qt E2E and 10 consecutive complete clean test cycles (with real independently evaluated model results) required for release, reset 0 after any defect.
4. Until then keep status **NOT RELEASE / 0 of 10 confirmed Windows E2E**. Do not issue a user release.

Cost/effort: prioritize architecture, reliable bounded model calls and reproducible tests. Do not attempt to bypass access to missing external services or commit credentials.

## Execution note (2026-11-06)
The initial cloud Codex invocation was blocked by an unsupported `--full-auto` flag (the API key was present). This run must use the repaired `codex exec --sandbox workspace-write` invocation. Do not treat that launcher failure as a defect in project source; focus on the complete P0 architecture task above.

## Cloud execution preflight (2026-11-06, third invocation)
The second workflow reached Codex but API responses returned HTTP 401 (no Authorization header). The cloud launcher now performs ephemeral Codex CLI API-key authentication from GitHub Secret via stdin before execution. If auth fails, stop and report; never print credentials. Continue the full architectural workstream unchanged.
