# Windows QA bootstrap and diagnostic handoff — 2026-11-15

Baseline: `627dde311f247324dc008dd386f9914bbd790ab7`, existing integration branch / PR #4.

## Confirmed incoming failure

The uploaded 2451-byte DIAGNOSTICS.zip has a valid internal manifest. Its
Windows 11 / Python 3.12.10 x64 bootstrap used the system Python, without
PySide6, gradio_client, pytest or pytest-subtests. RTX 3080 Ti 12288 MiB and
NVIDIA driver 616.92 are reported by nvidia-smi; no application Qt/CUDA,
inference, reviewer qualification or full tests began. The folder name claims
627dde3, but the old early-failure archive contains no source manifest, so its
source bytes cannot be certified. See input-summary.json for the archive hash
and sanitized facts. Raw user paths/archive are not committed.

The preceding handoff omitted one-time Python setup for a new source folder.
A GitHub source ZIP does not carry the prior checkout's .venv.

## Connected changes

- Explicit SETUP_WINDOWS_QA.cmd prepares only this checkout's .venv with free
  requirements-qa.txt packages, checks Python 3.12 x64 / venv, pip consistency,
  actual dependency imports and discovery. It neither installs ML weights nor
  modifies model-profile interpreters. Setup is separate from QA; running QA
  never installs anything. An explicit interpreter override remains explicit.
- Offline preflight reports all missing/incomplete dependencies and the selected
  interpreter before config parsing, setup wizard or model calls. pytest 8's
  plugin and pytest 9's built-in subtests capability are supported explicitly.
  Dependency discovery is not a GUI/native-runtime acceptance claim.
- Early failure archives now include source hashes and Python-environment
  evidence. The single ZIP also retains previously omitted native preflight,
  VRAM, encoder and preliminary-attestation JSON evidence; linked supervisor
  files are refused. Launcher displays the actual blocked reason and remedy.
- Saved QA/model settings and the existing counter/quality rules are preserved.

## Executed validation (Linux, Python 3.12)

```
QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python -m pytest -q ci/test_windows_qa_environment.py ci/test_windows_qa_source.py ci/test_qa_single_archive.py --tb=short
AI_SMM_TEST_ROOT=/workspace/work/pr4-qa-bootstrap/ci-data QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python -m pytest -q ci --tb=short --junitxml=/workspace/work/pr4-qa-bootstrap/ci.xml
AI_SMM_TEST_ROOT=/workspace/work/pr4-qa-bootstrap/dev-data QT_QPA_PLATFORM=offscreen /workspace/work/venv/bin/python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/pr4-qa-bootstrap/developer
/workspace/work/venv/bin/python ci/verify_tree.py --output /workspace/work/pr4-qa-bootstrap/tree.json
git diff --check
```

- Focused controller/bootstrap/archive: 38 passed.
- Full CI: 586 passed + 94 subtests; 2 native Windows-only skips.
- All 263 developer modules: 2109 passed + 1062 subtests, zero failures/skips.
- Structural/mirror/dependency integrity: PASS, 177 runtime modules.
- New functional regression launches the actual supervisor using an empty venv
  without pip, verifies all missing dependencies, unchanged saved settings,
  zero test runs, exact source hashes and archive-manifest hashes. No download.
- Initial focused run caught omitted archive entries and the existing runtime's
  built-in pytest 9 subtests. Both causes were fixed before the full final run;
  no check was bypassed and no PASS was substituted.

Application runtime bytes are unchanged. The source-QA controller and new CMD
setup script are not physically Windows-tested here. No Windows/CUDA/model
quality PASS is claimed; target stays 0/10. Paid API, Actions, models, release,
merge and main were not used or changed.

## Next physical step

In this source checkout on RTX 3080 Ti: SETUP_WINDOWS_QA.cmd once, then
RUN_WINDOWS_QA.cmd -> 1. Setup downloads free Python dependencies only.
If setup fails, send Desktop/AI_SMM_REPORTS/SETUP_CONSOLE.txt. Otherwise send
Desktop/AI_SMM_REPORTS/DIAGNOSTICS.zip after the QA attempt. Existing local
model profiles and settings stay in their current locations.
