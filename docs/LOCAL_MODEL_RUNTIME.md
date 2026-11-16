# Explicit local GGUF text runtime

Ollama remains the default. An optional already-running llama.cpp server can
serve local text generation and the semantic dialogue through the same model
boundary, cancellation checks, response contracts and diagnostic history.
The app does not download weights or start a server automatically.

Example configuration for an operator-managed loopback server:

```text
AI_SMM_LOCAL_LLM_BACKEND=llama_cpp
AI_SMM_LOCAL_LLM_MODEL=<exact server model alias>
AI_SMM_LOCAL_LLM_URL=http://127.0.0.1:18080
AI_SMM_LOCAL_DIALOGUE_MODEL=<exact server model alias>
AI_SMM_MODEL=<exact server model alias>
AI_SMM_DIALOGUE_MODE=local
```

The alias must match exactly. Other aliases retain their existing transport;
selecting this backend does not silently redirect other models. Local mode
prevents dialogue fallback to a cloud provider. No cloud credential is supplied
by this transport. Only an explicitly ported loopback URL (optionally `/v1`) is
accepted; redirects, credentials and inherited HTTP proxies are disabled.
JSON Schema, output token limits, complete-response checks and one normalized
completion are enforced. A valid JSON response does not prove semantic quality.
API reference: https://github.com/ggml-org/llama.cpp/blob/b12202/tools/server/README.md .

Development-only smoke, with native FFmpeg/ffprobe and PySide6 installed:

```text
python ci/studio_media_dialogue_smoke.py --real-model
```

This flag requires the explicit loopback backend, sets both text/dialogue model
aliases to that local model, and uses a fresh disposable application root. It
calls the real model, then checks exact source membership, slideshow parameters,
owned media publication, original source hashes, original dialogue receipt and
native worker shutdown. Without this flag only the semantic answer is a fixture;
Pillow/FFmpeg, Qt and SQLite still execute. `--cancel --corrupt-paid-ledger`
exercises local cancellation independently of the optional paid journal.

Current Linux/CPU model evidence is recorded in DEVELOPMENT_STATE.md. Small
model success/failure is scoped to that exact test and model. It does not certify
arbitrary dialogue, generative image/video editing, Windows, CUDA or RTX 2080
Super performance. Paid API calls, automatic Actions, builds and release remain
disabled; mandatory Windows acceptance is a separate gate.

## LM Studio and the installed Qwen2.5 14B

The existing shared text gateway also supports an explicitly selected LM Studio
model. Ollama stays the default; LM Studio is a separate local installation.
The route is `lmstudio/<exact id from the server>`, including any publisher path.
The adapter reads `/api/v0/models` before each completion and accepts only one
matching **loaded** `llm`. It never calls load, download or unload endpoints.
It sends JSON Schema as `response_format.json_schema` to `/v1/chat/completions`;
it does not reuse llama.cpp-specific schema/thinking fields. Incomplete replies,
wrong model IDs, invalid UTF-8 and redirects fail. Inventory and completion share
one deadline; the existing device lease/cancellation boundary remains in force.

API contracts checked 2026-11-16:
[model inventory](https://lmstudio.ai/docs/developer/rest/endpoints) and
[structured responses](https://lmstudio.ai/docs/developer/openai-compat/structured-output).
This integration requires those endpoints on a loopback server. Authenticated
LM Studio servers are not supported by this adapter; cloud keys are never reused.

### Focused Windows check, before another full media run

1. In LM Studio, manually load the already installed Qwen2.5 14B Instruct and
   enable its local API server. Configure at least 8192 context tokens for the
   reviewer controls. Keep the model loaded throughout the diagnostic; disable
   automatic eviction/JIT loading for this check in the server settings.
2. In the current source checkout, prepare `.venv` once with
   `SETUP_WINDOWS_QA.cmd` if this folder has not been prepared. Then run
   **`RUN_WINDOWS_REVIEWER_QA.cmd`**. The first run offers LM Studio selection,
   reads actual loaded model IDs, and lets you choose the existing matching GGUF
   file. It preserves the current media profiles, plan and Ollama vision model.
   Choosing “No” checks only the existing Ollama roster.
3. Send **`%USERPROFILE%\Desktop\AI_SMM_REPORTS\DIAGNOSTICS.zip`**.
   `reviewer_qualification.json` contains every candidate's six unchanged
   randomized controls, semantic verdicts, response/UTF-8 evidence and errors.
   `DIAGNOSTIC_COMPLETE` is limited to text controls: no full Qt/CUDA pass,
   preliminary attestation, release eligibility or target counter credit.

Settings are saved in the existing `AI_SMM_REPORTS/settings.json` as optional
`local_text` with `backend`, `url`, `model`, `model_file`. The same explicit roster
is used in full `RUN_WINDOWS_QA.cmd` runs. No inherited local/cloud environment
can add a model to source QA. The selected file's SHA-256 and server descriptor
are checked before/after runs. The file hash binds the user's file selection;
the API does **not** attest that those are the weights currently in server RAM.
Select the actual loaded file, and do not replace/reload it during the campaign.

Qwen2.5 14B, 7B and Coder share one reviewer family. Namespaces, quantization and
instruction suffixes cannot create an extra independent Qwen voice; common
Llama/Gemma publisher aliases are normalized too. Qualification still requires
all six controls and sufficient distinct qualified families beyond the author.
Unknown/custom aliases do not establish foundation-model independence by name;
do not rename the loaded model to manufacture a new family.

The server owns its GPU allocation. AI SMM does not kill or unload that process.
A 14B GGUF of approximately 9 GB plus its working memory cannot fit completely
in the target's 8 GB VRAM: CPU offload/context limits must be configured and
measured on that PC. A resident text model also reduces memory available for
image/video generation. The existing 6 GiB image / 10 GiB video free system-RAM
floors remain unchanged. No Qwen quality, target performance or resource PASS is
inferred from the model name or successful HTTP contract tests.

For ordinary application use (separate from source QA), explicitly set:

```text
AI_SMM_LOCAL_LLM_BACKEND=lm_studio
AI_SMM_LOCAL_LLM_URL=http://127.0.0.1:1234
AI_SMM_LOCAL_LLM_MODEL=lmstudio/<exact loaded server id>
AI_SMM_LOCAL_DIALOGUE_MODEL=lmstudio/<same id>
AI_SMM_MODEL=lmstudio/<same id>
AI_SMM_DIALOGUE_MODE=local
```

The Qt text workers and reviewer discovery then use the shared gateway. Saved
reviewer admission is invalidated when the backend, endpoint or QA weight hash
changes. One configured LM Studio model is supported at a time; additional
independent families come from the existing approved Ollama roster.
