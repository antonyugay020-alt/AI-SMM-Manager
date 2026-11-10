# Offline generative integration — development evidence

Baseline: `211e8976fe4780b1d5768a42303a58764d9e00fc`.
Tested source: `600ae799b6e067be7a8936eb2bb01f26cb260a12`.
Existing integration branch / draft PR #4; main unchanged. Source only: no build,
archive, release or merge. No paid model API or GitHub Actions execution.

## Implemented vertical slice

InstructPix2Pix, SD 1.5 + IP-Adapter, SD inpainting and AnimateDiff Video-to-Video
now have explicit pinned offline model installation/profile contracts and a
separate Diffusers runtime. Model/config hashes, safe weight formats, local-only
loaders, immutable source snapshots and no cloud fallback protect execution.

Actual Qt profile loading persists in SQLite. Generative controls select one
source, a separate reference/mask, prompt, dimensions, seed, steps and strength;
video adds interval/frame/FPS controls. Semantic issued-media actions and the
existing selected-image dialogue/manual local entry points use the durable
local dispatcher. Results, source/model/seed/device receipts, history, ready
media, cancellation and recovery share existing ownership/publication services.

Native FP16 CUDA policy, bounded shapes, offload/VAE/chunking, RAM/VRAM preflight,
OS device serialization, parent pipe watchdog, explicit failures and retained
private diagnostics account for the 8 GB target without claiming it was tested.
Project deletion rejects active local claims. Recovery cleans only the locked
job's partial work and adopts a verified committed result without re-inference.
No latent-step resume or cross-hardware bitwise determinism is claimed.

## Final checks on the exact source

Linux / Python 3.12 / PySide6 6.12.0. Main interpreter:
`/workspace/work/venv/bin/python`; isolated ML interpreter:
`/workspace/work/generative/venv/bin/python`. ML: Torch 2.8.0+cpu,
Diffusers 0.36.0, Transformers 4.57.3, Accelerate 1.12.0, Safetensors 0.7.0.
All app regression state uses `AI_SMM_TEST_ROOT` under `/workspace/work/generative`
and `QT_QPA_PLATFORM=offscreen`. Main and ML `pip check` both PASS after isolation.

- `python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/generative/sealed-regression`:
  **2109 tests + 1042 subtests PASS**, 263 modules, JUnit 3151, zero failures,
  errors or skips.
- `python -m pytest -q ci --junitxml=/workspace/work/generative/sealed-ci.xml`:
  **273 tests + 77 subtests PASS**. Includes semantic profile/ID binding,
  mask compositing, model manifest tamper rejection, actual separate-process
  lock exclusion/waiting/release, parent EOF shutdown, real FFmpeg video/audio
  composition with explicitly fixture-generated frames and deletion fencing.
- Entire CI repeated with `gc.collect()` after every case: **273 + 77 PASS**.
- Actual neural CPU image check:
  `OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-profiles.json --profile tiny-topology-only --corrupt-paid-ledger`:
  PASS, actual existing Studio panel → offline Diffusers subprocess → owned
  result/receipt/ready media, original unchanged, corrupt paid ledger independent.
- Actual neural CPU temporal/video check:
  same smoke with `--profiles /workspace/work/generative/tiny-video-profiles.json --profile tiny-video-topology-only`:
  PASS, actual AnimateDiff temporal network → 4 frames at 64x64/8 FPS → native
  FFmpeg video with original interval audio → owned result and clean shutdown.
- Same actual image process route with `--cancel`: PASS, persistent cancellation,
  no artifact/publication, original unchanged. This cancellation occurs during
  child startup; it does not prove interruption of an actual target CUDA kernel.
- Actual CUDA-profile probe: **EXPECTED_BLOCKED / CUDA_NOT_AVAILABLE**; no CPU
  fallback, emulation, published output or paid API. This is not a GPU PASS.
- Tree/mirror verification PASS: 157 runtime modules, 773 source entries;
  fingerprint `52ed31e0494ad60392d571eb9e28477c95275fb747d9c81304c101e97feea2c0`.
  `git diff --check` PASS. Manifest additionally hashes CI/tool Python sources.

## Scope of real neural evidence

The CPU image topology derives from the public pinned HF internal tiny test
model, converted with `torch.load(weights_only=True)` to safetensors for this
isolated fixture preparation. Its tiny UNet is expanded to 8 input channels;
it is not a trained instruction editor. The temporal test uses tiny pretrained
image components and an **untrained random MotionAdapter**. Saved construction
scripts/manifests document these transformations. No unsafe weight loader is
used by the application/production installer. No weights/binary build are
committed or delivered here.

These runs establish actual Diffusers tensor/API execution and Qt/SQLite/media
lifecycle, not useful prompt/reference/temporal quality. Full-size IP-Adapter,
inpainting and trained video quality still require real target hardware. The
Windows harness explicitly rejects tiny/fixture provenance; CPU runs cannot
satisfy Windows/CUDA or quality gates.

## Defects / failed iterations retained

1. A combined ML/main install revealed an incompatible Hugging Face Hub range
   between Transformers and Gradio. Resolved with a separate explicit ML Python
   in each frozen profile; both resulting environments have clean dependencies.
2. Tiny-fixture preparation first used the wrong Transformers safetensors
   filename. Corrected the fixture preparation to `model.safetensors`; this is
   not a production model download/quality PASS.
3. Initial real image execution failed after inference with only generic output.
   The old buffered-stdin watchdog shutdown defect was then reproduced directly:
   buffered daemon read exits SIGABRT with `_enter_buffered_busy`; raw `os.read`
   exits cleanly. Replaced the watcher and retained bounded model failure logs
   in SQLite before temporary cleanup. The original failed run is not PASS.
4. Actual temporal inference rejected generated dimensions: omitted video
   width/height allowed Diffusers defaults instead of the requested canvas.
   Added explicit geometry, bounded VAE decode and host output readback, a
   targeted call-contract regression and a successful real temporal rerun.
5. Forced-GC CI overlapped a real neural smoke and two process tests accidentally
   contended for the real global device lock. Isolated their lock files/workloads
   without mocking locking or the actual EOF watcher. Generation now waits for
   a busy device, remains cancellable and acquires after owner death. Final
   complete CI passes with normal and forced GC.
6. Audit closed two related lifecycle gaps: project deletion could race a local
   claim, and crash-abandoned partial work could accumulate. Deletion now rejects
   active local jobs; only a recovered, OS-locked task's prefixed work is cleaned.

## Confirmed remaining blocker and next exact step

This Work executor is Linux without NVIDIA device/CUDA and has no configured
Windows/RTX endpoint. Actual CUDA absence was tested; it is not an assumption.
No authorized paid runner/GPU provisioning or Actions may be substituted.

Prepared: [setup and constraints](../../LOCAL_GENERATIVE_MEDIA.md),
[Windows case plan](../../WINDOWS_GENERATIVE_PLAN.example.json), actual
`generative_acceptance.py` and mandatory `local_generative_media` in the full
Windows runner plus ten-pass supervisor. Actual Windows/RTX execution, all four
trained engines, artifact receipts and interactive visual verification are
required; missing host/models/plan/review blocks PASS. Other full Windows,
clean-unpack and independent-review requirements are unchanged.

Next requires an accessible user-authorized **Windows + RTX 2080 Super 8 GB**
host: install the pinned trained weights/isolated CUDA runtime, adapt/run the
six-case plan, inspect full-size instruction/style/background/object/detail and
reference/video/audio quality, fix actual device/model failures, then perform
ten successive full Windows Qt E2E runs on one frozen source. **Windows 0/10**.
Do not resume by repeating the earlier text/slideshow integration stage.
