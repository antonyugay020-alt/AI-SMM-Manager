# Controlled integration checkpoint — paid Kie media boundary

Baseline HEAD: `5f7922ddd01fb9d443c29ec833b1609b8d302ac7`, branch `codex/pr4-media-atomic-recovery-20261107`.
Integrated source commit: `0d28fe66326730b5b36c01dda8a8205c35efdc10`.
Resulting checkpoint: HEAD of this branch containing this file, with GitHub
tree evidence. The source commit is **not** an acceptance certificate.

## Coherent integrated milestone
- Brought staged `DurableCostGate` and `CostGate` plus Kie paid client,
  task lifecycle and gateway into all three mirrored runtime trees.
- One-use provider/task grant and durable reservation are required even for
  direct Kie transport; uncertain paid network results retain the reservation.
- Qt monthly budget now operates on an explicitly injected shared durable
  ledger; when absent, the control is disabled rather than showing a false
  spending limit. Offline draft/preview and project switching remain intact.
- Existing nonempty legacy Kie allocations block paid gateway dispatch
  pending provider reconciliation; no historical expense is silently erased.
- Added offline regressions for low-level bypass, owner fencing, quote/cap
  limits, replay/restart, Qt budget binding and legacy-ledger freeze.

## Evidence / defect inventory
- Source and test files were constructed as one Git tree from the baseline
  plus the staged budget architecture. Three source mirrors use identical
  Git blob SHA for each changed module.
- Exact command NOT RUN: `python -m unittest discover -s ci -p 'test_kie_media_*.py'`
  (private repository checkout unavailable in execution container; GitHub
  Actions runners currently fail before steps). No test is claimed PASS.
- Earlier successful Linux 4/4, Windows 4/4 and aggregate gate apply only
  to an older SHA; they do not certify this HEAD.
- Independent reviewer verdict for this HEAD: NOT ACCEPTED.
- Interactive real-model Windows Qt E2E: **0/10**.
- No paid CI/API launched. PR #2, PR #4 and main remain unchanged.

## Next milestone
- Wire one durable ledger instance into the real Qt composition root and
  Kie gateway, reconcile legacy Kie reservations with provider evidence.
- Confirm documented provider upload/status routes; then test full GUI media
  lifecycle and project restore on native Windows.
- Only after explicit CI/API budget authorization: exact-SHA Linux/Windows
  regression shards, aggregate, independent review and 10 Windows Qt E2E.
- No PR merge, release, or acceptance until all gates are met.

## Post-integration source review
- The disconnected Qt budget editor is disabled immediately at widget
  construction, not only after the first refresh.
- Legacy SQLite reopen test explicitly closes the reopened connection on
  Windows before temporary-directory removal.
- GitHub blob identity checks are source-structure verification only; no
  Windows/Linux Python regression or live GUI acceptance was run.
