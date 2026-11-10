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
