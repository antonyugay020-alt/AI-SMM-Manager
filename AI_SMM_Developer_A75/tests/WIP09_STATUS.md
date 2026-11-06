# AI SMM Manager R97 recovery WIP09 — NOT RELEASE

Source: saved WIP08 private checkpoint. This is a recoverability and Windows QA preparation step, not a claim that lost R98-R106 features have been restored.

Changes:
- Recover asset-only deletion after process termination: normalized originals/<file> manifest entry is accepted.
- Block traversal, symlink and junction paths during deletion recovery.
- Resolve Windows launcher regression test from actual source layout, not current shell directory.
- Include program and mirrored developer test source side-by-side in guarded Windows QA kit.

Local Python evidence: 1415/1415 in 5 successful consecutive runs; 20 targeted engineering suites PASS, no ResourceWarning in successful runs. Interrupted runs were not counted. No live API calls were made.

Release blockers: Native Windows 11 Qt/PySide6 GUI E2E, real Ollama and OpenAI content quality, full original functional requirements and lost R98–R106 reconciliation are NOT VERIFIED. Production release is BLOCKED by release_guard.

Windows QA procedure (disposable test data only):
1. Extract this acceptance kit into a NEW folder, keeping both sibling folders.
2. Open AI_SMM_Work_Architecture_A75/RUN_ARCHITECTURE_CHECK.cmd.
3. It installs PySide6 and other dependencies and runs windows_acceptance.py on the actual application and a disposable project. This may issue real AI requests if enabled in the user's environment, including billable OpenAI requests; only enable with permission and budget controls.
4. Collect VALIDATION_RESULTS.zip and the Windows screenshot. Never treat a missing test, failed test or blocked provider as a release PASS.

Do NOT use the package as the user's production release.
