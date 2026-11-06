# AI SMM Manager — WIP07 (NOT RELEASE)

This is an internal recovery branch built on the preserved WIP06 code line, not the lost R106 source tree.

## Changes
- A process-local OpenAI credential source is shared by text API requests, GUI OpenAI image-edit workers and cloud model discovery.
- The GUI can explicitly clear that process-local credential. No API key is put into environment variables by this mechanism; an externally set environment credential remains untouched.
- Explicit environment mappings in provider tests/callers are isolated from the process-local key.
- Previous Qt project/task isolation, exact task IDs, independently persisted paused tasks, media validation and truthful export failure reporting remain part of the source tree.

## Evidence and limits
- Run `python -W error::ResourceWarning -m unittest discover -s developer_tests -p 'test_*.py' -q` from the source folder (with the correct developer_tests directory and imports).
- The validated Python regressions and ZIP checks are recorded in the checkpoint validation folder; read the evidence there.
- All OpenAI network interactions in this stage are mocked. No paid API calls were made here.
- The current host is Linux without PySide6 and without the user's Windows/Ollama setup; actual Windows GUI, live AI generation, full editorial quality and every advertised workflow have NOT been certified.
- Release must remain blocked until evidence for the exact final source fingerprint covers these checks. Never present this checkpoint as a user-ready build.
