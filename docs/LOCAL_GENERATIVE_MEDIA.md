# Offline generative media integration

Development source, not Windows/GPU/quality acceptance. No automatic downloads,
paid API fallback or paid ledger dependency. Native FFmpeg/Pillow operations
remain separate from these Diffusers neural engines.

| Task | Engine / free local weights | Roles |
|---|---|---|
| image_instruction | timbrooks/instruct-pix2pix | Source + text instruction: style, background, objects/details |
| image_reference | SD 1.5 + h94/IP-Adapter | Source, separate reference, text prompt |
| image_inpaint | SD 1.5 inpainting | Source, same-size mask, text; white changes, black is preserved |
| video_generative | SD 1.5 + AnimateDiff motion adapter | Existing clip, optional separate IP-Adapter reference, text |

Model repositories are pinned by immutable revision in
`tools/install_local_generative.py`. Source licenses/model cards are included in
verified bundles when provided. SD 1.5 carries CreativeML OpenRAIL-M terms;
InstructPix2Pix/IP-Adapter and AnimateDiff code have their own upstream terms.
Free local execution does not mean unrestricted model licensing or guaranteed
instruction-following/temporal quality.

## Setup and Qt controls

Keep the main PySide6/Gradio environment separate from ML: their supported
Hugging Face Hub versions can conflict. Create a second virtual environment,
install the appropriate official PyTorch 2.8.0 CUDA wheel for the host/driver,
then install `AI_SMM_Work_Architecture_A75/requirements-generative.txt` there.
Do not substitute CPU wheels for NVIDIA acceptance.

Example from the repository on a prepared Windows host (paths are examples):

```powershell
py -m venv C:\AI-SMM-ML
C:\AI-SMM-ML\Scripts\python.exe -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu126
C:\AI-SMM-ML\Scripts\python.exe -m pip install -r AI_SMM_Work_Architecture_A75\requirements-generative.txt
py tools\install_local_generative.py --engine instruct_pix2pix --destination C:\AI-SMM-Models\instruction --python C:\AI-SMM-ML\Scripts\python.exe
```

The installer command is a **dry run**: it fetches free model metadata and
prints required storage; add `--download` to download the pinned weights.
Other engine values: `sd15_ip_adapter`, `sd15_inpaint`, `animatediff_v2v`.
The installer never overwrites an existing bundle/profile. It checks LFS SHA-256
or immutable Git blob identity, rejects pickle/Python payloads, and writes a
manifest covering all local bytes. FP16 component bytes receive standard
safetensors filenames; their hashes/provenance remain recorded. Video bundles
include motion and reference encoders and require several GB of disk space.

Load the generated `*-profiles.json` through **Медиа · задания → Подключить
локальные профили…**. The configuration path persists in the application's
SQLite settings. Alternatively set `AI_SMM_GENERATIVE_CONFIG` explicitly;
that environment setting takes priority on startup. Multiple profiles can be
combined in one version-1 `profiles` mapping. Every profile contains `engine`,
absolute `bundle`, `manifest_sha256`, `device` and the separate `python` path.

Choose one primary registered source, a model, the separate reference/mask,
prompt, resolution, steps, seed and strength. For video choose start, frame
count and FPS. The ordinary local image button and selected-image semantic
editing also use compatible installed profiles. Semantic media tasks receive
only issued project resource IDs/profile names, never authority to choose raw
model paths or commands. The model selects the operation and reference/mask
roles; host code checks their technical validity and frozen configuration hash.

## RTX 2080 Super 8 GB envelope

- Native CUDA FP16, never BF16/emulation; CUDA availability and free memory are
  checked before model loading. No automatic CPU/cloud fallback.
- Images: 64–512 pixels per side, multiples of 8; 2–50 steps. Video: at most
  384 per side and 4–16 frames at 1–12 FPS. Start with **256x256, 8–16 frames**.
- Model CPU offload, VAE slicing/tiling, video feed-forward chunk size 1 and
  decode chunk size 1. Explicit width/height reaches the video pipeline so its
  default resolution cannot silently enlarge a task.
- Preflight requires at least 6 GB total/3 GB free VRAM; available system RAM
  at least 6 GB for images or 10 GB for video. These are fail-closed lower
  bounds, **not evidence that every permitted task fits**. Actual peak CUDA
  allocation and library versions are saved in execution receipts.
- OOM is an explicit retained failure. Lower dimensions/frames/steps in a new
  task; the application never silently changes an existing request or quality.

Inputs are padded to the explicit canvas without cropping or stretching.
Inpainting composites the generated masked region with the unchanged prepared
source outside the mask. This preserves black-mask pixels on that canvas;
resizing/padding itself is a technical transform and is not original-resolution
pixel equality. A video task generatively transforms the selected short interval,
then remuxes that interval's source audio. It does not silently replace/overwrite
the entire original video or claim unlimited-length generation.

## Ownership, stop and recovery

Jobs reuse SQLite local-media requests/claims, immutable owned input snapshots,
events, original dialogue links, verified ingest and project history. Dedicated
model execution receipts include model-manifest digest, seed, device/dtype,
library versions and actual output hash. Protected inputs remain unchanged.
Results are also staged in the existing project's ready-media queue.

Only safetensors/config/tokenizer bundles are accepted; complete file hashes
are checked on the worker and child, using local-files-only loaders and offline
Hub settings. Each task binds its exact profile/interpreter/weights and source
receipts. No shell command is built from model output.

An OS device lock serializes generation and waits cooperatively when busy.
The GUI owns the subprocess. Stop terminates it, waits for exit and fences late
publication; parent death closes a pipe and the child's raw-FD watchdog exits.
OS locks release on process death. Restart removes only partial work directories
bound to the recovered, locked job; it leaves other jobs and legacy unknown
work directories untouched. Project deletion rejects live local claims.
Errors retain bounded private diagnostics in SQLite even after temp cleanup.

Interrupted unpaid generation restarts from the beginning using the same saved
request/seed/model/source identity. This is **not latent-step resume**, and a
seed is not a cross-version/hardware bitwise reproducibility guarantee. A
committed artifact/receipt is adopted without another inference. Changed inputs,
profiles/weights or missing execution receipts fail closed.

## Verification boundaries

`ci/studio_local_generative_smoke.py` requires an explicit installed profile and
runs the real Studio panel, actual offline child inference and real SQLite/media
publication. The development CPU image/video checks used tiny test topologies,
including an untrained motion adapter. They prove API/tensor/lifecycle execution;
they do **not** establish useful image/video quality or simulate RTX acceptance.

`generative_acceptance.py --plan <plan.json> --output <folder> --review` requires
actual Windows + RTX 2080 Super 8 GB, all four non-fixture engines, actual Qt
controls, real inference/output receipts and interactive visual inspection.
An explicit plan is mandatory; see `docs/WINDOWS_GENERATIVE_PLAN.example.json`.
It retains artifacts/hashes and never advances the ten-run counter itself.
Without `--review` the result is only technical evidence pending visual review.

Set `AI_SMM_GENERATIVE_ACCEPTANCE_PLAN` for the full Windows acceptance runner.
`local_generative_media` is now mandatory in both the full runner and the
external ten-consecutive-run supervisor. Missing host/models/plan/review blocks
PASS; a failure resets the existing supervisor counter. The other full Windows,
clean-unpack, text/model and independent-review gates still apply. No such run
has been executed on this source in this Linux cloud session: **0/10**.
