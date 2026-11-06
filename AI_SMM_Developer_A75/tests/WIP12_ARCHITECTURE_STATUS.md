# AI SMM Manager / WIP12 / NOT_RELEASE

Source: WIP11 Windows QA + actual Windows report 2026-10-31.

## Architecture changes
- `workflow_authority.py`: immutable task ID + request hash + publication scope;
  typed execution outcomes and recovery directives (not model self-approval).
- `compound_tasks.py` uses authority for generation receipts. Complete outputs
  remain `ready_for_persistence`, never `completed` until final independent
  review and storage verification.
- `completion_gate.py` and `app.py`: check exact SQLite task claim against
  execution receipt before applying documents; recheck after storage.
  Wrong project, task ID, request digest, cadence, or unfinished count cannot
  be promoted to complete, even if the model claims PASS.
- `compound_retry.py`: separate unavailable review from failed content;
  do not regenerate accepted artifacts merely because reviewer infrastructure
  failed.
- `execution_environment.py` and `windows_acceptance.py`: isolate unit-test
  subprocess from live provider credentials, global reviewer rosters and
  qualification certificates. Live runs retain their separately authorized
  provider configuration.
- New randomized execution-contract tests use a fresh seed and unique task
  identities each run. Regression fixtures remain fixed to reproduce defects.

## Explicitly NOT verified
- Windows GUI / PySide6 after these changes.
- Real new OpenAI/Ollama SMM output quality, reviewer reliability and complete
  autonomous delivery. Prior WIP11 Windows QA failed 6 of 7 workflow cases.
- Full feature coverage of historical unsaved R98-R106 work.

## Release gate
NOT_RELEASE: Windows GUI + real-model acceptance remains required. Do not treat
unit tests, mock qualification responses, or ZIP verification as acceptance.
