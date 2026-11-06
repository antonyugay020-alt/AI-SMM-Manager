# AI SMM Manager — continuation of draft PR #2

Run the **integrated Qt task lifecycle development** on the existing branch
`codex/architecture-42191626323`. This is the branch with 8/8 Linux/Windows
Python shards and a PASS aggregate gate as recorded in CI run 42194186707.

The next workstream must add actual integration code and reproducible tests for
Qt application launch, project creation and switching, independent model review,
one-off tasks, result validation, restart and worker disposal.

Do not modify main application code directly. Do not merge or release the draft
branch. An offscreen Qt smoke test is not equivalent to the mandatory 10 native
Windows interactive real-model E2E runs. New code resets the previous CI PASS
status until the full regression runs again.

Model cost should be bounded to the prescribed single integrated workstream.
All source and test changes should remain on PR #2.
