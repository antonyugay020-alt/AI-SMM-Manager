# AI SMM Manager — R97 recovery WIP08 (NOT_RELEASE)

The user-facing Windows release remains blocked until real Windows Qt E2E and live model quality acceptance.

## Changes
- Single recoverable project deletion transaction used by both local and AI-assisted destructive UI routes. Files are staged on their own volume and tied to a SQLite commit marker; startup reconciles unfinished operations.
- Project ownership and symlink protection for deletion paths, dedicated failure injection tests for before/after commit, neighbor-project isolation, and crash-state recovery.
- Test framework corrects source-root selection and prohibits vacuous import-cycle tests or developer/test source drift.

This is a PRIVATE development snapshot, not evidence of a Windows release.
