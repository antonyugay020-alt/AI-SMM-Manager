# Instructions for long-running Codex / ChatGPT Work development

## Objective
Produce an actually usable, integrated Windows application, not a stream of commits or progress notes. Keep working through multiple coherent implementation, integration, regression and root-cause-fix cycles in the *same available work session*. One modified file, one test or one commit is not a completion criterion.

## Mandatory session workflow
1. Check live branch HEAD, draft PR and `docs/DEVELOPMENT_STATE.md` (if present), `docs/CONTROLLED_INTEGRATION.md`, actual tests and known blockers.
2. Preserve existing user work and prior architectural decisions. Work on this branch; never modify `main`, silently create a new competing PR, merge, or release.
3. Prioritize an end-to-end vertical slice across implementation, persistence, lifecycle, UI and failure paths. Detect common root causes rather than patching each repeated symptom.
4. Write/modify real code and meaningful regression tests. Run full available local regression, then integration, restart, concurrency and failure recovery scenarios. Fix and rerun. Clearly distinguish isolated tests from full checkout regression and actual Windows E2E.
5. Keep a short durable source-SHA-linked checkpoint with commands/results, integrated capabilities, unresolved blockers and the next exact step. On resumed sessions, use it rather than restarting analysis.
6. Use remaining session capacity for the next linked development step. Do not stop after creating a note, PR comment or a single commit.
7. Do not spend on GitHub Actions, API providers or other services without explicit authorization. Do not commit credentials. If a particular test runner/provider is unavailable, keep making independently verifiable progress and mark unrun tests honestly.
8. No interim builds, fake successful acceptance or routine reports. Final release requires comprehensive Windows clean-unpack GUI E2E and project-specific acceptance gates.

## Security and independence
The software's hard safety boundaries are not configurable by AI. Maintain independent projects and avoid changes to unrelated repositories. Disable potentially dangerous production actions until separately verified and explicitly authorized.

## Current development target: AI SMM Manager
Continue existing PR #4 integration branch with PR #2 dependencies (do not discard or overwrite distinct changes). Complete autonomous Windows Qt project management and text/media workflows: image styling/background/details based on prompt/reference, short-video editing from inputs, optional Kie.ai/cloud media services, local Ollama-first execution and free providers when suitable, independent verification, atomic file/SQLite effects, cancellation and reliable recovery.
Monthly paid cap defaults to user-approved $20 (configurable); paid service calls require user confirmation and durable preflight spend reservation. Never double-pay on recovery, and never silently charge. Target deployment RTX 2080 Super 8GB. Integrate actual Qt controls, backend, budget journal and recovery and test the entire workflow; required 10 successive clean full Windows Qt E2E checks before release. No chargeable GitHub Actions or model API usage without approval.
