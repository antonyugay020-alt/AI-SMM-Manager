# Semantic media / live-owner recovery development evidence

Baseline: `abd33acac6c9ca6e2828ff1d5ee041840bf8492f`.
Tested source: `0ab7cf578b94eabf186ab85588aae2b5941a375b`.
Linux, Python 3.12, PySide6 6.12.0; native Pillow/FFmpeg/ffprobe.
Development evidence, not release/Windows acceptance.

## Integrated boundaries

- Semantic local media operation across dialogue contract, issued owned media
  IDs, live SQLite source checks, immutable source receipt hashes, durable job,
  actual Qt worker, independent output readback, project artifact, original
  dialogue completion, history, explicit retry, cancellation and shutdown.
- Five executors: image_resize, video_cut, video_format, video_montage,
  video_from_images. Availability reflects installed tools and source kinds.
  Host paths/hashes are never supplied by the model. This does not implement
  generative style/background/detail changes with Pillow or FFmpeg.
- Native per-job OS locks exclude recovery/duplicate execution while a live
  worker owns the attempt. A real separate-process test confirms no fencing
  during execution and safe recovery/retry after process termination. Locks
  remain on disk to preserve inode identity; OS ownership dies with process.
- Optional explicit loopback llama.cpp transport, schema forwarding, bounded
  request/response, no proxy/redirect/cloud credentials and common model-call
  cancellation/diagnostics. Ollama remains the default. See LOCAL_MODEL_RUNTIME.

## Final verification

Interpreter: `/workspace/work/venv/bin/python`. App checks explicitly isolate
state with `AI_SMM_TEST_ROOT` and `QT_QPA_PLATFORM=offscreen`.

- `python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/session2/semantic-lock-final-regression`:
  **2109 passed + 1037 subtests**, all 263 developer test modules.
- `python -m pytest -q ci --junitxml=/workspace/work/session2/semantic-lock-final-ci2.xml`:
  **251 passed + 77 subtests**.
- Same complete CI with `gc.collect()` after each case: **251 + 77 PASS**.
- `python ci/studio_media_dialogue_smoke.py`: PASS, actual Studio dialogue,
  semantic model fixture, real slideshow, exact geometry/timing/source set,
  original intake resolved once and clean native shutdown.
- `python ci/studio_media_dialogue_smoke.py --cancel --corrupt-paid-ledger`:
  PASS, cancelled task/intake, no published artifact, originals unchanged.
- `python ci/native_media_processing_acceptance.py`: PASS for real local pixel
  edit, FFmpeg trim, project ownership and original integrity.
- `python ci/verify_tree.py`: PASS, 152 mirrored runtime modules, 757 source
  entries, fingerprint `87ccb823f561805615e6400c58ecaa3fc1561062c61d2567aea0e47da87f413a`.
- `git diff --check`: PASS. Source manifest also hashes CI Python scripts,
  which the historical runtime fingerprint does not include.

## Failures and root causes retained

1. Real Qwen 0.6B Studio first attempt failed its intake; no complete diagnostic
   was retained by the initial disposable harness. This is FAIL, not PASS.
2. The second attempt records a real semantically incorrect `create_text`
   decision for a request to create a slideshow. Its unrelated text-model
   transport was not running. Required nullable media parameters and complete
   smoke failure diagnostics now prevent a missing media field/diagnostic gap;
   they do not prove the tiny model can understand arbitrary instructions.
   The opt-in real smoke now binds both text/dialogue aliases to the selected
   local server and disables cloud dialogue fallback.
3. New live-owner recovery caused an old CI expectation to fail: that test
   expected an actively running worker to be interrupted by recovery. Corrected
   it to verify the live worker completes once, and added a distinct orphan
   recovery/retry case. A separate subprocess verifies OS lock release on death.
4. Native smoke invoked without `AI_SMM_TEST_ROOT` failed on this sandbox's
   read-only default Documents path. Repeated with an isolated writable root;
   no application source was changed for this environment mistake.

No paid API calls or Actions dispatched; no user build, archive, main change,
merge or release. Automatic paid independent review remains disabled.
Windows full GUI/clean-unpack/target model acceptance remains **0/10**.
Actual generative image/video quality, RTX 2080 Super, Windows msvcrt lock path
and independent real-model review remain unverified. Logs from mocked provider
contracts cannot replace those checks.

## Follow-up from actual model failures

Final tested source: `ab4cc3b83824bd33912bf1c7fb3b4d14d967219a`. The real Qwen 1.7B run selected the correct
local tool but supplied trim options for a slideshow, then changed operation
on retry while retaining media parameters. Retained as FAIL. The native schema
now uses task variants with exact source kinds/cardinality and null-only unused
options, matching the host validator. No user wording is classified by code.

Final full developer regression: **2109 + 1037 PASS**; complete CI **252 + 77
PASS**; forced-GC complete CI **252 + 77 PASS**; both final Studio fixture
checks PASS. Final runtime fingerprint: `c95931f3345bd4eb70595936304a22f17766a72be959d631fdee7eed0732845c`.
The extended real smoke deadline permits the existing two 180-second model
calls and native media work. This does not alter production model budgets.

Final live-model check **PASS** on `ab4cc3b83824bd33912bf1c7fb3b4d14d967219a`:
`AI_SMM_LOCAL_LLM_BACKEND=llama_cpp AI_SMM_LOCAL_LLM_MODEL=qwen3:1.7b-gguf AI_SMM_LOCAL_LLM_URL=http://127.0.0.1:18080 QT_QPA_PLATFORM=offscreen python ci/studio_media_dialogue_smoke.py --real-model`.
Actual Qwen3-1.7B Q8_0 on CPU selected video_from_images with both issued
sources, 128x96 geometry and 0.5 seconds per image. Actual Studio/FFmpeg execution,
source integrity, one project result, original dialogue receipt and native
shutdown all passed. Final model request used 389 generated tokens; server
reported 117.15 seconds (overlapped full regression). No supplied semantic reply
or paid API was used in this final check. This is **one exact scenario**, not
arbitrary dialogue or generative image/video model quality acceptance. Earlier
0.6B and broad-schema 1.7B failures remain failures in this evidence.

Next: connect local generative image/video engines to the same durable ownership,
recovery and output-verification boundary; verify actual instruction/reference
quality and RTX 2080 Super 8GB behavior; run independent review and the required
Windows clean-unpack/full-GUI checks. Windows acceptance remains **0/10**.
