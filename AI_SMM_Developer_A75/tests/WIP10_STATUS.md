# WIP10 — internal architecture checkpoint, NOT RELEASE

Base: saved WIP09 checkpoint (not reconstructed R106). No user data or API keys included.

Critical fix: local image generation and local image editing now own their launch project and a reserved output path. Their completion handlers validate the image, exact path and project, then commit to SQLite before displaying success. Duplicate signals, corrupt images, symlinks and uncommitted transactions are rejected. The local generator is now included in the global worker activity guard; starting a second media worker while another operation runs is blocked. Added isolated tests for ownership, late paths, duplicate completions, invalid files, symlinks, commit boundary and GUI handler wiring.

Release blockers remain: Windows 11 native Qt GUI end-to-end, live Ollama/OpenAI output quality, full original requirement reconciliation (including lost R98–R106). Python unit tests and structural inspections do not substitute for those checks. The Windows QA kit is a tool for validation, never a release.
