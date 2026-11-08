# Current development state — offline generative image/video engines

Existing integration branch / draft PR #4. Baseline `211e897`; tested source
`600ae799b6e067be7a8936eb2bb01f26cb260a12`. Main unchanged. No build/archive,
release, merge, paid model API or Actions execution. Windows E2E **0/10**.

- Added pinned offline InstructPix2Pix, SD 1.5 IP-Adapter/reference editing,
  SD inpainting with protected mask compositing, and AnimateDiff V2V with
  optional reference and original interval audio. Only explicitly installed
  safetensors/config bundles; immutable manifests and a separate ML Python.
- Integrated actual Qt profile controls/settings, typed semantic issued IDs,
  selected-image local entry points, shared SQLite dispatcher, source snapshots,
  output verification, model/device/seed receipts, ready media and task history.
- Native FP16 CUDA/Turing policy, image/video caps, offload/chunking and RAM/VRAM
  checks; no BF16/emulation or automatic CPU/cloud fallback. Device contention
  waits cooperatively. OS locks, raw-FD parent-death watcher, child termination,
  persistent cancellation, retained errors, orphan cleanup and receipt adoption
  share existing ownership. Project deletion now rejects live local claims.
- Prepared strict actual Windows/RTX six-case generation/visual-review harness.
  Its result is a mandatory full-QA and ten-pass-supervisor gate; absent actual
  host/models/plan/review cannot count. [Setup, envelopes and exact scope](LOCAL_GENERATIVE_MEDIA.md).

Final full Linux developer regression: **2109 + 1042 subtests PASS** (all 263
modules); full CI **273 + 77 PASS**, also with forced GC after every case.
Actual existing Studio panel → separate Diffusers neural process → SQLite,
owned image/video/audio result → clean shutdown PASS on **tiny CPU test
models**. Image check also passes with a corrupt optional paid ledger;
child-start cancellation prevents publication. Tiny instruction/motion models
are not trained quality evidence. Full-size reference/inpaint/trained video
quality and GPU performance remain unverified.

Actual CUDA profile probe returned `CUDA_NOT_AVAILABLE`, without output,
emulation, CPU fallback or paid call. This Linux Work executor has no NVIDIA
CUDA device and no configured Windows/RTX host. Both isolated dependency
checks, source mirrors (157 modules) and diff checks PASS. Source fingerprint:
`52ed31e0494ad60392d571eb9e28477c95275fb747d9c81304c101e97feea2c0`.
[Exact source manifests, real/fixture scopes, failed iterations and logs](evidence/20261108-offline-generative).

**Next needs user-provided Windows/RTX access**: run the trained engines and
visual fixture plan on real RTX 2080 Super 8 GB, correct measured model/VRAM/
quality defects, then perform the required ten successive full Windows Qt E2E
checks. Current tiny CPU inference does not advance acceptance counters.
Independent real-model review and final packaging/clean-unpack gates still
block delivery. Automatic paid independent-review workflow remains disabled.

---

# Previous development state — semantic media and live-model integration

PR #4 stays draft on `integration/ai-smm-pr2-20261106`; main untouched.
Tested source: `ab4cc3b83824bd33912bf1c7fb3b4d14d967219a`, following
`0ab7cf578b94eabf186ab85588aae2b5941a375b` and prior checkpoints below.
No user build/archive, release or merge. Windows acceptance **0/10**.

- Dialogue now dispatches all five supported local media operations using only
  issued project-media IDs: resize, trim, format, montage and slideshow. Source
  ownership/current metadata/receipt hashes, immutable worker snapshots, exact
  task parameters, independent output geometry/duration/audio checks, atomic
  publication, original dialogue receipt, history and cancellation are joined.
- Task-specific native JSON variants constrain source kinds/count and null-only
  unused parameters. The same constraints remain enforced by the host. No
  phrase/keyword routing or raw model-supplied path grants were introduced.
- Live per-job OS locks prevent queue recovery from fencing an active worker,
  including across processes. Process death releases ownership; orphan recovery
  and explicit retry keep the same durable task identity. Windows lock branch
  still requires actual Windows verification.
- Optional exact-alias loopback llama.cpp backend uses the existing common text
  boundary and schemas, no cloud credentials/proxy/redirect. Ollama is still
  default; see [configuration and smoke](LOCAL_MODEL_RUNTIME.md).
- **Real local Qwen3-1.7B Q8_0 CPU Studio scenario PASS**: Russian request →
  actual model decision → FFmpeg slideshow from both issued images → exact
  128x96/0.5 seconds per image → owned result/intake → clean shutdown.
  Weights and official llama.cpp binary SHA-256 were verified. This is one live
  semantic scenario, not general model/Windows/GPU acceptance. Qwen0.6B and the
  earlier broad-schema 1.7B runs failed; their diagnostics remain recorded.

Final complete Linux developer regression: **2109 + 1037 subtests PASS**, all
263 test modules. Complete CI **252 + 77 PASS**, also with forced GC after every
case. Both Studio fixture checks (success and cancellation with a corrupt paid
ledger) PASS; native photo/video checks PASS. Three runtime mirrors PASS (152
modules), `git diff --check` PASS. Final fingerprint:
`c95931f3345bd4eb70595936304a22f17766a72be959d631fdee7eed0732845c`.
[Exact commands, source manifests, failures and final evidence](evidence/20261108-semantic-local-model).

No paid API calls or Actions dispatched. Automatic paid independent review
remains `disabled_manually`; commits use `[skip ci]`.

Next: integrate actual local generative engines with the same durable media
lifecycle, then verify real instruction/reference editing quality and target
RTX 2080 Super 8GB behavior. FFmpeg/Pillow are not generative styling engines.
Live paid-provider/independent model review and ten consecutive clean complete
Windows Qt E2E checks remain mandatory, unrun, and block release. A known tiny
model failure must not be treated as a successful general AI acceptance.

---

# Previous development state — native local-media dispatcher

PR #4 remains draft on `integration/ai-smm-pr2-20261106`. Tested source:
`cad9bd89564153f7f45ec29cef11824b7637affe`, following the verified
`3ed71fc` / evidence `62f7e40` milestone. Main is untouched. No release/build.

- Actual Qt local execution now supports image resizing, video trimming,
  montage, format conversion and video from photos, independently of API keys
  and the paid ledger. Mixed audio/silent video montage preserves an audio
  track. Non-generative tools do not claim AI styling or object editing.
- One project-source ownership boundary and one byte validator now serve local
  and Kie backends. Immutable snapshots, input receipts, attempt fencing,
  cooperative stop, subprocess termination, event history and crash-safe
  ingest/publication are shared across the local job lifecycle.
- Semantic `video_trim` uses this dispatcher and a persistent link to the
  original dialogue. Result publication resolves that intake once. Crash after
  artifact commit recovers its dialogue receipt without rendering twice.
- Reference use follows semantic booleans/explicit user choice through GUI,
  workers and the standalone editor. Removed keyword-driven reference/pixel
  decisions. Explicit technical tonal controls remain distinct from AI editing.
- Local editor precision queries CUDA capability and native BF16 support;
  it selects FP16 rather than BF16 emulation on a Turing-class device. This is
  mocked capability verification, **not** a real RTX 2080 Super model run.
  API reference: https://docs.pytorch.org/docs/stable/generated/torch.cuda.is_bf16_supported.html .

Verification on exact source fingerprint
`f2870fe3cfd31c155d44a0f82e8b4a79de55bb1bfbb6af3fa4465f525ebe3802`:
full developer regression **2109 + 1035 subtests PASS** (263 modules, JUnit
3144); offline CI **236 + 77 PASS**; forced-GC whole CI **236 + 77 PASS**;
real native media checks PASS. Actual Studio source import → semantic trim →
local FFmpeg worker → owned result → original dialogue receipt → clean close
PASS, including a second isolated run with a deliberately corrupt paid ledger.
All three runtime mirrors and `git diff --check` PASS.

Commands/interpreter/environment match the previous milestone below; latest
regression output: `/workspace/work/session2/local-verified-regression`.
[Exact logs, manifests and failure inventory](evidence/20261108-local-media-dispatcher).
No paid API calls, Actions, merge or user build. Automatic paid review stays
disabled. Windows clean-unpack/full-GUI/real-model acceptance remains **0/10**.

Next: extend the semantic contract/dispatcher binding to every supported local
media task using issued project-media IDs, then verify available generative
model adapters without inventing availability or model-quality evidence.
Real local model weights/services, target GPU performance, independent model
review and Windows E2E are still unverified. Earlier PASS is not final acceptance.

---

# Previous integrated milestone — 2026-11-08

Development evidence only. PR #4 remains draft; Windows acceptance **0/10**.

## Source

- Branch: `integration/ai-smm-pr2-20261106`.
- Baseline: `019c1e1e8bd7679c04041460532d3bf0f566ed87`.
- Tested implementation: `3ed71fc5ee50cc1d5fa5b2613d9a92603a18bae6`.
- The evidence commit containing this checkpoint follows that implementation.
- Main remains `0c3b42f714a63afd3b2308136753e4d0dabacd10`, untouched.
- Prior descendant history and the previous checkpoints are preserved in Git.

## Integrated milestone

- Shared durable cap and one-use, project/task/request-bound approval for paid
  OpenAI/Anthropic text; exact model/messages/options checked before dispatch.
  Competing grants cannot reserve the same operation twice. Unknown outcomes
  retain reservations across restart. Direct free-tier calls require explicit
  account free-tier acknowledgement.
- Real Qt text-approval broker: worker waits cooperatively while GUI confirms
  a user-verified maximum and price source, reserves against the same ledger,
  and journals authorization evidence. Decline, cancellation, project switch
  and changed requests fail closed. No provider tariff is guessed.
- Legacy OpenAI image editing now uses the same durable gate and exact
  source/reference/prompt binding; a directly constructed worker cannot bypass
  it. Prior batch confirmation cannot silently bypass per-operation pricing.
- Ollama-first dialogue for all wording; removed phrase-list route selection.
  Schema errors do not trigger another model/provider. Paid fallback still
  requires explicit approval.
- Actual Qt media controls create/edit images, reference-to-video and video
  transformation requests with project-registered sources. Hashing/validation
  and upload run on worker-owned SQLite connections. Unpaid intents persist;
  upload needs separate consent. Source snapshots prevent upload races.
- Operator-confirmed maximum price/reference, expiring payload-bound quotes,
  a separate one-request confirmation and global reservation precede paid Kie
  generation. This is a manual verified-quote flow, not automatic tariff lookup.
- Persisted local media stop, safe confirmed-task resume, no remote-cancellation
  claim. Stop is checked at transport/download/publication boundaries and inside
  the ingest transaction. An in-flight paid response still preserves its ID.
- Explicit uncertain-submission recovery: user supplies Kie log ID/evidence;
  read-only status must prove identical original model/input and a dispatched
  durable reservation before attachment. No createTask retry or automatic refund.
- Active text-task cancellation atomically fences the execution/registry in
  ApplicationRuntime; shared model boundaries stop additional calls and late
  responses cannot publish. Actual Studio cancellation and clean shutdown tested.
- Qt worker ownership, weak window references and deterministic test-native
  object disposal address reproduced GC/deferred-deletion crashes. Final tests
  run with ordinary GC and with forced collection after every CI case.

## Exact verification

Linux/Python 3.12/PySide6 6.12.0. All provider calls are fixtures, not live APIs.
App checks use `AI_SMM_TEST_ROOT=/workspace/work/session2/test-root` and
`QT_QPA_PLATFORM=offscreen`, interpreter `/workspace/work/venv/bin/python`.

- `python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/session2/saved-regression`:
  **2109 passed + 1031 subtests**, 263 modules, JUnit 3140, no failures/skips.
- `python -m pytest -q ci`: **213 passed + 77 subtests**.
- Whole CI repeated with `gc.collect()` after every case: **213 + 77 PASS**.
- Actual Studio native cancellation/ownership/shutdown smoke: PASS.
- `python ci/native_media_processing_acceptance.py`: real local photo edit,
  FFmpeg trim, original integrity and project publication PASS.
- `python ci/verify_tree.py`: all three mirrors byte-identical, 146 runtime
  modules, fingerprint `989713a757665e67971bc8ce17a10954f59161efb75719d25b93a7156a0a39b9`.
- `git diff --check`: PASS.

[Logs, manifest and failure inventory](evidence/20261108-media-text-cancellation).
The failed iterations and the GC-disabled diagnostic are not acceptance PASS.

## Next linked milestone and remaining gates

Connect the verified local FFmpeg/Pillow operations to the durable dispatcher,
shared project history/recovery and the actual media queue, instead of only a
route preview. Then integrate available local generative-model capabilities
without pretending that FFmpeg supplies AI image/video styling. Ollama and
ComfyUI services/model weights, live generative quality and target RTX 2080 Super
8GB performance are unverified/unavailable here.

No paid API or Actions run was invoked. Paid independent-review automation stays
`disabled_manually`; commit tips use `[skip ci]`. No user build, ZIP, merge or
release. Live Kie/provider validation, independent real-model review and ten
consecutive clean full Windows Qt E2E runs remain mandatory and unrun. Linux
checks cannot advance Windows counters or authorize final delivery.
