# Crash-recoverable document-effect migration — 2026-11-10 (unreleased)

Baseline `6b057e35d96f7d392cb1260eb3b5cf62ba058345`, existing draft PR #4. The previously atomicized outbox migration still could not **recover databases already stranded by an older non-atomic migration**. When `ai_file_effect_outbox_v1` survived an interrupted rename/copy, `CREATE TABLE IF NOT EXISTS` could create an empty new table and skip the orphan, silently hiding pending receipts. The schema installer is now a shared, savepoint-composed `file_effect_schema_recovery.install_schema`, called by all three identical `file_effect_outbox` mirrors. It recognizes rename-only, empty, partially copied and fully copied replacement states; reconciles all 13 receipt fields by immutable row ID; recreates the pending index on the active table; and removes the legacy table only after an exact merge. Divergent duplicate IDs, ambiguous tables and unknown legacy columns fail closed, preserving both tables and the outer transaction.

Verified **16/16 isolated SQLite pytest cases** (11 new recovery scenarios + five existing atomic migration scenarios), Python 3.13.5, and **300 deterministic randomized crash-stage recovery cycles**. The new recovery module and test source were tested locally before the commit. All runtime copies in Work, Developer and Developer/tests are kept byte-identical. These tests do not represent the complete original 263-module + all-CI regression or real Windows Qt/CUDA acceptance; RTX 2080 Super remains **0/10**. No Actions, paid APIs, downloads, main merge, live bot changes or reboot. Next gate remains a complete frozen-HEAD offline regression, then consolidated Windows preliminary and ten target runs.

---

# Atomic legacy task identity and document-effect migrations — 2026-11-10 (unreleased)

The same transaction-boundary audit that covered the document-effect outbox also identified a multi-row legacy task registry backfill with no savepoint. A conflict in the second pending assignment could leave the first task and its context partially migrated, while an application transaction remained open. `task_registry.ensure_registry` now uses the shared `sqlite_transaction_core.compose` boundary around schema creation, index, legacy task rows and context binding. The installer either commits its own complete migration or joins an existing application transaction without committing it. Both this change and the preceding outbox schema upgrade preserve legacy records, do not touch running tasks, and do not use paid providers.

Three `task_registry.py` runtime mirrors updated identically. New `ci/test_task_registry_atomic_migration.py` checks two-row success/idempotence, conflict after one would-be migration, outer rollback, context binding and missing legacy table. **5/5 isolated SQLite tests passed** on the matching migration logic under Python 3.13.5, plus **5/5** earlier outbox migration tests. No full original 263-module/complete CI regression has been executed on this HEAD; no Windows Qt/CUDA QA, RTX 2080 Super still **0/10**. No release/merge/Actions/API/model download/live-bot change/reboot.

---

# Atomic document-outbox schema migration — 2026-11-10 (unreleased)

Baseline `1fa9b44a1f1c8a1e14f342531de2a78fbab857de` on draft PR #4. The document-effect outbox had a legacy SQLite ALTER/CREATE/COPY/DROP migration without a transaction when invoked from a clean replay connection. A process crash between DDL statements could strand the sole receipt table under a temporary name and prevent document completion. The migration now uses the existing shared `sqlite_transaction_core.compose` savepoint: schema installation, legacy copy, index and cleanup are atomic, and an enclosing document transaction is never committed by the installer. No change to existing effect ordering, authorization, paid APIs, media jobs or runtime paths.

The three `file_effect_outbox.py` mirrors are identical. New `ci/test_file_effect_schema_atomic.py` exercises fresh schema, idempotence, preserved pending legacy receipts/attempts, outer rollback, simulated mid-migration exception and rollback of new schema. **5/5 isolated SQLite tests passed** on the matching install logic under Python 3.13.5. This is not the 263-module + complete CI regression of the frozen final HEAD and not Windows Qt/CUDA QA. Acceptance remains RTX 2080 Super 0/10; no release, merge, paid service, GitHub Actions, downloads, bot/monitor change or reboot.

---

# Shared numeric media contract and model-schema parity — 2026-11-10 (unreleased)

Baseline: `f0d8ea6e555090563efc7b7070724d5a17ffc696`, existing draft PR #4 only. This work closes a systemic split between the model-facing local media JSON Schema and the host's `validate_generation` scalar constraints. In particular, the schema previously advertised arbitrary nonnegative `video_generative.start` while the host rejects values above 3600 seconds; all other width/height/steps/seed/strength/guidance/frames/fps limits were duplicated in separate implementations, permitting future silent drift and unnecessary model retries.

The `generative_contract` now owns shared scalar limits and projects fresh per-task `generation_scalar_schema` dictionaries. `local_media_contract.local_media_schema` consumes this projection directly, including the 384-pixel video bound, 8-pixel dimension alignment, and 3600-second video-start bound; the host reads the same limit tuples. Source-role ordering, installed-model binding, host cross-field denoising validation, and actual media execution remain fail-closed and unchanged. All changed runtime files are written as identical blobs in Work, Developer and Developer/tests.

**Verification on exact proposed source blobs, not an old result:** the two updated Python files were independently reconstructed in an isolated Linux/Python checkout and their Git blob SHAs verified as `76873183fd3d47636249fe683351e24595f949bf` and `c0b10c5cb2178aafb2df70ab5dca7e42114a0ad2`. The new CI test blob `b4502c80ddcf6faa626346d3eeac6ef075849ba3` was likewise byte-identical. Actual `pytest` on those three exact blobs: **6 PASS**, exercising 5,724 deterministic randomized scalar boundary comparisons across four generative tasks, schema projection, video limits, mutable-alias isolation and cross-field strength guard. `compileall` PASS. This is **targeted CPU proof only**: no original full-checkout 263-module/48-CI regression, Qt, Ollama, CUDA, model quality or Windows QA is claimed.

Next gate: complete frozen-HEAD offline 263-module + all CI regression, then one consolidated Windows RTX 3080 Ti QA, then ten consecutive RTX 2080 Super full E2E (still **0/10**). No Actions, paid APIs, downloads, live-bot changes, main merge, or Windows reboot.

---

# Ordered media-source role schema integration — 2026-11-10 (unreleased)

Baseline `72dccbafacf96b6851193d531ff5aeee9afed032` in existing draft PR #4. Root cause: model-facing `video_generative.source_ids` previously used the union of **all** issued video and image IDs in either position. The host already enforced first=video and optional second=image; thus valid-looking structured model plans could be rejected downstream, causing avoidable AI retries and mismatched advertised capabilities. A single `generative_source_ids_schema` now derives per-position `prefixItems` from the existing authoritative `generative_contract.source_kind`. The video-only case exposes one source slot, while video+image enables a second reference slot; image instruction/reference/inpaint roles remain unchanged. `items` retains the union as a conservative legacy-decoder fallback; the host remains fail-closed on incorrect roles.

Changed runtime source is identical in Work, Developer, Developer/tests. New `ci/test_generative_source_role_schema.py` checks schema/host role alignment, reversed video/reference, two videos, missing image reference and no-video capabilities. A standalone Python 3.11+ JSON Schema Draft202012 simulation of the proposed role constraints passed 12 checks (not a complete checkout or an actual Ollama decoder). **The complete 263-module and CI regression on the resulting HEAD has not been executed.** Windows RTX 3080 Ti and RTX 2080 Super full Qt/CUDA E2E remain unverified (0/10). Do not use this checkpoint as a release or paid-service authorization. Next gate: complete frozen-HEAD offline suite, then one consolidated Windows QA; no main/Actions/model downloads/live-bot changes.

---

# Media QA source integrity and objective evidence contract — 2026-11-10 (unreleased)

Baseline for this integrated draft PR #4 change: `2e66e2f0122d95f903b17c19ba0e205a56b3cf13`. Scope: QA provenance, independent objective metrics, real-media final readback, and CPU regression. **No release and no completed full original-repository regression**.

**Systemic issue:** the previous review receipt was cryptographically bound to the generated output but **not to the source image/video, reference or mask** used for semantic evaluation. An original could change between generation and vision/human review, while the previously computed output digest stayed valid. The parent also accepted incomplete or physically impossible objective metric values (NaN/zero change, invalid frame/audio evidence) when a semantic reviewer returned PASS.

**Unified correction:** `qa_receipt_provenance` now captures SHA-256 for every case source, reference and mask in the same fresh request as the output, validates their bytes before and after the owned child review, and demands the identical source-hash vector in the returned receipt. The parent revalidates output and sources before accepting either PASS or a legitimate objective BLOCKED. The actual Windows GUI acceptance rehashes the originals and generated output **after** automatic/human visual review before recording a case PASS. `validate_media_metrics` independently enforces finite, meaningful image contrast/change, protected-mask evidence, video dimensions/duration/three-frame samples, flicker bounds and preserved source audio with measured correlation; video objective metrics now explicitly report `source_audio`. The separate Windows native child remains bounded and OS-owned; no additional paid provider or model is involved.

Work, Developer and Developer/tests copies of all four changed runtime modules must remain byte-identical. The associated CI files now cover source mutation/forged provenance, image/video evidence, owned-process mutation and final GUI readback. A standalone Python 3.13.5 CPU reconstruction of the **exact pure provenance Git blob** `3bf9fda82434f9692d46f1c9b662ab427414c1e0` passed 13 direct tests, plus 5 isolated worker-contract cases (18/18). The worker-contract tests were run against a locally reconstructed pure function, not the complete Windows worker; do not report them as full source regression, actual child/Qt/CUDA acceptance, or media-quality proof.

**Still required:** complete 263-module + entire CI regression on the final frozen PR HEAD; independent actual Windows RTX 3080 Ti preliminary and ten consecutive RTX 2080 Super Qt/CUDA E2E (still **0/10**). Do not run paid Actions, APIs, model downloads or interfere with live bots. Keep the single Windows QA archive path `Desktop\\AI_SMM_REPORTS\\DIAGNOSTICS.zip`.

---

# Quality-review receipt provenance hardening — 2026-11-10 (unreleased)

A full semantic verdict was previously accepted without binding its evidence to the **current** child invocation or the exact output bytes. The QA output folder may be reused; an earlier `quality.json` could survive a successful but non-writing child. This is now addressed as one lifecycle contract: parent deletes stale receipt/error, creates a fresh 128-bit request ID and SHA-256 of generated media, child verifies output bytes before and after objective/semantic review and includes both in PASS/BLOCKED receipts, and parent re-hashes output and checks both before accepting any status. A failed child cannot turn a prior report into PASS. Objective quality rejection still remains BLOCKED rather than a native infrastructure PASS.

Work/Developer/Developer-tests copies of the worker and new pure provenance module are synchronized; CI mocked-child and real isolated subprocess fixtures now cover repeated folder, stale receipt, mismatched nonce, mutated output, SHA proof and ordinary objective rejection. The new pure provenance module's six CPU cases passed in an isolated Python 3.13.5 environment. Additional offline tests against the exact Git-blob-matching worker and helper completed **21/21 isolated CPU cases** (6 pure, 9 mocked ownership, 6 real Python child using a synthetic vision model and simplified local process adapter). The new CI test explicitly inserts the Work source directory before importing the pure helper. These do **not** prove the original Windows native-process implementation, GPU, visual quality, or the full repository regression. **Full original-repository 263-module and complete CI execution remains unverified on this new source; no Windows Qt/CUDA run, 3080 Ti preliminary or 2080 Super 10/10 claim.** No GitHub Actions, paid API, downloads, main merge, live bot/monitor change or reboot. Next gate remains complete frozen-HEAD offline regression, then single preliminary Windows QA.

---

# Quality-proof closure after native QA ownership — November 9, 2026

**Source updated on existing draft PR #4**, starting from `a421aa79b2d2e0d52f344026d48edf879f79c7f4`. Real Windows QA has **not** been repeated, target remains 0/10.

Root issue in the previous owned quality-review interface: the Qt parent only checked `quality.json.status`, `model`, and `digest`. Thus a fabricated or prematurely incomplete child `{"status":"PASS","model":...,"digest":...}` receipt could certify a semantic QA step despite having no objective-media metrics or independent reviewer verdict. **This is now rejected by the supervisor**. The new `validate_quality_receipt` checks concrete objective measurements, all explicit review fields, image instruction/preservation PASS, reference/mask PASS when present, video temporal PASS with sampled frames, and nontrivial reviewer observations. For an objective rejection, a nonempty `objective_rejection` remains BLOCKED; hard errors still abort. The child itself still uses the previously established bounded, OS-owned subprocess (900s) and preserves process log/error receipts.

Changes applied to all three identical `qa_quality_worker.py` mirrors plus the mocked and real-child CPU CI fixtures. The synthetic child tests now return full metrics/verdict objects, not status-only fake successes. New missing-verdict, reference-scope and temporal-evidence rejection tests added. Independently checked six isolated pure receipt decisions, including one completed image PASS, a legitimate objective BLOCKED, and four malformed/unsupported PASS rejections. These are not physical CUDA/Qt/vision tests. GitHub tree confirms **165 shared Work/Developer modules byte-identical**, five critical modules all match across Work/Developer/Developer tests; 263 developer tests and 46 CI test modules present. **Full test execution on final source is not yet proven**.

**Next required gate**: run complete offline 263-module + full CI suite on the exact final PR HEAD, then one Windows RTX 3080 Ti preliminary, then ten consecutive RTX 2080 Super full E2E only after preliminary PASS. Live admission must establish two qualified independent reviewer families; the past Windows Ollama roster did not show Qwen2.5:14b, and no model was installed or billed. A real uncertain watercolor visual review remains a quality BLOCKED. Never count synthetic tests as user-facing image quality. Preserve single user-visible output `Desktop\\AI_SMM_REPORTS\\DIAGNOSTICS.zip`; no changes to main, paid services, GitHub Actions, bots, or Windows restart.

---

# Unified Windows QA quality-review ownership — verification checkpoint (2026-11-09)

Baseline `1658c05eb8aaabba3505117416456ed413bd5e1a`; current draft PR #4 only. This integrated change addresses a **systemic** unbounded-operation risk in all real-media acceptance. Previously, the GUI waited forever in `ThreadPoolExecutor` for a `quality_callback` to finish; a stuck HTTP/Ollama/native media worker could strand the entire QA despite a bounded Diffusers generator. The old thread path has been eliminated, not merely given an ineffective timeout.

- New `qa_quality_worker.py` (identical Work, Developer, Developer/tests mirrors) executes objective media analysis and independently qualified local Ollama vision inside a dedicated OS-owned child. Windows Job Object/native parent-death ownership applies before application imports; parent Qt pumps events and enforces a 900-second overall limit, captures an isolated `quality_process.log` and `quality_process_error.json`, and kills only its own child on timeout. The semantic proof stays bound to model tag and 64-character digest; no network except previously configured local Ollama, no paid APIs, model downloads, global taskkill, or source overwrites.
- `generative_acceptance.py` demands `run_with_pump` for any automatic quality callback **before any expensive native GPU work**; the legacy unbounded thread executor is removed. `windows_acceptance.py` supplies `BoundedQualityReview` after actual pixel-control qualification of the chosen local vision model.
- `local_media_quality.py` now raises `ObjectiveQualityRejected(ValueError)` for nine **actual output-defect criteria** (blank/unchanged image or video, invalid/missing inpaint mask protection, flicker, wrong video duration/size, lost/changed audio). The QA child persists these as `BLOCKED` per-engine verdicts, allowing the remaining independent engines to be checked in the same Windows run. **Infrastructure errors** (Ollama contract, HTTP, OS, codec, process timeout) remain hard failures. No uncertain or failed vision result is turned into PASS.
- Tests `ci/test_qa_owned_quality.py` and `ci/test_qa_owned_quality_subprocess.py` include mock ownership, real isolated CPU child startup, failure-mode identity checks, rejection, objectively invalid media, failing codec, and bounded hang cases. Executed **12 local isolated checks PASS** with exact Git-blob-matching `qa_quality_worker.py`, `native_process_runtime.py` and subprocess test source, using a synthetic local quality provider only (no Ollama/CUDA). GitHub blob hashes and synchronized triplicate mirrors checked: **165 shared runtime modules, zero differing pairs**. The synthetic child **does not establish actual media quality**.
- **Not complete:** Full original-repository 263-module/complete CI regression of this new head was unavailable in this chat environment; do not reuse older successful counts. Actual native Windows Qt/CUDA, two independently qualified local editor families, semantic watercolor/media results and 10 consecutive clean target 2080 Super runs remain **BLOCKED / 0 of 10**. The older Windows 3080 Ti result proves native Diffusers inference can finish but previously rejected output quality. Current exact source has not been accepted on Windows. No release, PR merge, main modifications, paid services, Actions, or changes to running bots/monitors.

**Release protocol** remains one complete offline regression at final frozen HEAD (fix all failures as a group), then one 3080 Ti Windows preliminary, then ten 2080 Super runs. One user-visible archive path only: `Desktop\\AI_SMM_REPORTS\\DIAGNOSTICS.zip`. Do not claim success after an isolated fixture or force the user into per-defect repeated QA.

---

# Cohesive AI SMM media-strength integration — post 966d285 (2026-11-09)

Current integration branch PR #4, baseline `966d285bff6dda5b2844c697edf41c85c99089d7`. This checkpoint records *code changes* and a limited verification scope, **not a release**.

**Root cause found in the real media pipeline:** Qt exposes and persists the `strength` control for all four local generation engines, but InstructPix2Pix previously ignored it and always passed `image_guidance_scale=1.5`. The prior real Windows image result (source QA `bdc7477`) completed inference but was rejected by independent vision as not clearly watercolor. This omission made the user-facing control ineffective for that engine; it is not evidence that changing the scale alone solves visual quality.

**Integrated architectural change:** a single `generative_contract.instruction_image_guidance(strength)` maps the existing 0.05–1.00 editing strength inversely to image-guidance 1.95–1.00; other engines retain native denoising-strength. The step-count limitation now applies only to models that actually denoise based on strength. Work/Developer/tests mirrors of contract, runner, Qt and Windows acceptance were changed identically. The runner writes *requested strength and actual Pix2Pix image guidance* to its execution receipt. Native Windows E2E rejects a generation if those exact parameters do not match the QA request. The Qt label now describes the real function. New CPU pipeline-argument/receipt test in `ci/test_generative_media.py` and eight pure contract tests in `ci/test_instruction_strength_contract.py` exercise both behavior and bounds.

**Actual verification:** GitHub tree audit found **164 common Work/Developer modules byte-for-byte identical**, including all three copies of four modified files. The exact Git blob `112b38d24eee8f7da1a980d8292bcb6518df70f6` of `generative_contract.py` was reproduced in the isolated container and passed **14 direct contract/boundary cases**. This verifies only that pure module. The previously reconstructed ZIP-report smoke fixture ran 6 cases, but is not a full-checkout regression of current source.

**Still explicitly blocked:** Current HEAD has not yet run the full 263-module suite, complete CI, or actual Windows Qt/CUDA on the final code. The earlier Windows QA `bdc7477` had working native inference but `reviewer_models: FAIL`, `local_generative_media: FAIL`, and one Windows-specific CI test failure. The Windows-specific PID test was updated before this checkpoint but is still not proven on Windows. That QA Ollama inventory did **not** contain `qwen2.5:14b`; no model installation is claimed. Stronger visual transformation is unverified; visual quality and two independent reviewer families remain real release gates. **Target acceptance remains 0/10.**

**Next complete cycle:** a clean-checkout full offline regression of this final SHA (no paid Actions), consolidate all resulting defects, then one Windows RTX 3080 Ti preflight/e2e; only if PASS proceed to 10 clean RTX 2080 Super E2E sessions. No main merge or release until that evidence exists. User-visible QA archive remains at the single path `Desktop\\AI_SMM_REPORTS\\DIAGNOSTICS.zip`; preserve history internally.

---

# Windows QA bdc7477 — consolidated remediation (2026-11-09)

The actual Windows RTX 3080 Ti preliminary run tested source
`bdc7477e4971b3278982510fedf592d549f5861e`.
Received `DIAGNOSTICS(4).zip`; verdict remains **BLOCKED, 0/10**.
Native Torch/Diffusers preflights PASS, real InstructPix2Pix output and
SQLite receipt completed (old native-import stall not reproduced), 10366 MiB
free VRAM before case 0. Image quality was rejected: vision model
`gemma3:4b` returned UNCERTAIN and observed no convincing watercolor conversion.
This is **not** accepted as image-quality PASS.

Windows developer regression **263 modules / 2066 PASS / 1045 subtests**;
live Qt smoke PASS; source CI **339 PASS / 1 FAIL / 2 skipped /
77 subtests PASS**. CI defect: `test_actual_parent_death_stops_child_even_when_native_code_holds_gil`
compared Windows venv launcher PID with the actual Python interpreter PID,
which may differ due redirector process. Revised test now validates actual
interpreter ancestry, checks its termination on owner loss and handles
NoSuchProcess races; protection has NOT been relaxed.

Reviewer admission: **0 eligible qualified reviewers**, hence text work
and live full GUI are NOT_TESTED. The exact Ollama inventory contained
`qwen2.5-coder:7b, qwen3:8b, llama3.1:8b, deepseek-r1:8b, mistral:7b, gemma3:4b`
plus two cloud tags excluded from local-only QA. **`qwen2.5:14b`
was absent from the actual server inventory; prior 14B code support does
not install or admit a model automatically. Controls remain unchanged.

Integrated improvements on PR #4 after this run:
- one stable user-facing latest report ZIP:
  `%USERPROFILE%\Desktop\AI_SMM_REPORTS\DIAGNOSTICS.zip`; existing
  timestamped sessions remain internal for evidence; early Python bootstrap
  failure also yields a ZIP at the same path; fallback cannot reuse a
  previous successful run when the later run failed;
- independent media-quality failures are collected across the entire
  four-engine Windows QA matrix, without treating a rejected image as PASS;
  native/cancellation/infrastructure failures still fail fast;
- Windows native parent-death test follows actual interpreter PID while
  retaining full child ancestry and death assertions;
- targeted offline ZIP publication tests and fail-closed quality aggregation
  tests added; 4 standalone ZIP tests and 4 lightweight status examples
  passed locally. **The complete original-repository CI and native Windows
  tests have not run on this modified HEAD.**

Next development gate: complete offline regression on the final PR #4
source bytes; fix any failures together. On next real Windows QA check
all native process-tree, image quality, separate engines, text reviewer
family capacity, and receipt integrity. Do not ask user to browse nested
folders or repeat unfinished QA. Do not modify main, bots, monitors,
start paid APIs, GitHub Actions, merge or release.

---

# Current checkpoint — native startup and reviewer integration after c6d1ec8

Baseline `c6d1ec892b9409968d8ce5ba53cdaf45f45858d2`, same integration branch/draft
PR #4. Result is the commit containing this checkpoint. Shared isolated native
preflight/inference now imports Torch before Pillow/Python observer/GPU-lock,
loads framework classes under the common lease, and protects children with OS
parent-death ownership even when native code holds the GIL. Bounded diagnostic
and terminate-race cleanup paths are integrated. Qwen 2.5 14B is not hidden by
failed Coder variants; derivatives do not add an independent family vote.

Frozen runtime fingerprint:
`3e7b82e87c7e7ea0abd3f81540ced051413cf8497fda384de9a92907720f2125`.
Final Linux checks: all 263 modules **2109 + 1049 PASS**; real four-shard isolated
QA **2109 + 1048 PASS**; complete CI and forced-GC CI **340 + 77 PASS, 2 native
Windows-only skips**. Real isolated CPU Torch/framework preflight and actual
cached tiny CPU Qt image/video/cancel/owner-death/same-ID retry/SQLite receipt
recovery PASS. Mirrors/dependencies/bytes/diff PASS, 164 runtime modules.

[Exact code causes, commands, failures and retained evidence](evidence/20261109-c6d1ec8-native-startup).
Available cloud checks have completed successfully. The old Windows Torch
stall's precise native cause and its actual elimination remain unproven; no
Windows/CUDA or trained-model quality PASS is inferred. Live reviewer admission,
including the exact installed Qwen 2.5 14B candidate, remains a physical-host
gate with unchanged six controls. Target acceptance is still **0/10**.

Next physical phase uses the existing RUN_WINDOWS_QA.cmd / phase 1 on 3080 Ti
and the same Desktop AI_SMM_REPORTS root, preserving junction/history. No new
Windows run was required before cloud checks passed. No main/bot/monitor
changes, Actions, paid APIs, model downloads, merge, user build or release.

---

# Real Windows QA f967fdd — grouped findings and source remediations (2026-11-09)

- Source that ACTUALLY ran: `f967fdd961038fd5faf02a9f5a3c9e7facdba1da`, fingerprint `56e96005f249381818cf3559f9ebfa841b03c741dd7c5620cc8f10be61af0b2c`; RTX 3080 Ti, QA session `20261109T091019Z_preliminary_3080ti_544e9777`. Result **BLOCKED** (child exit 2), target 0/10. This source's acceptance ZIP is NOT evidence for the repaired head.
- Full actual Windows developer regression **263 modules / 2066 PASS / 1044 subtests**, Windows Qt smoke PASS, source CI **326 PASS / 77 subtests / 2 skipped**, vision controls PASS. No production release or media quality inferred.
- **Concrete media failure**: case 0 InstructPix2Pix; 10086 MiB free out of 12288 MiB before launch (PASS); acquired shared device lease; verified offline bundle; stopped at `importing_torch` for **480.1 s**. Parent failed with `LOCAL_MODEL_STAGE_TIMEOUT`. No evidence the image pipeline or CUDA inference had started. The same CUDA Python 2.8.0+cu126 had passed the independent runtime import preflight; this differential suggests (but does not prove) an initialization-order / native process-start interaction.
- **Independent reviewer failure**: only `deepseek-r1:8b` passed 6/6 control cases; `qwen2.5-coder:7b` 3/6, `qwen3:8b` contract FAIL, `llama3.1:8b` 4/6, `mistral:7b` 3/6, `gemma3:4b` 3/6. Five failures are semantic/contract errors, **not timeouts**. Live text pipeline/independent review/full GUI remain NOT_TESTED. Total Ollama tag count was 8, but this ZIP does not include the exact raw tag list or selection config; **do not assert that Qwen 2.5 14B was installed in this Ollama instance or why it was excluded**. Previous change added audited roster evidence for next actual run.
- **Integrated code changes after this Windows failure** (existing draft PR #4 only): split native Torch import into `initialize_python_torch` (before OS GPU lease) and leave CUDA/model load/inference within the lease; preserve per-thread native-import faulthandler stack evidence and cancellation; keep direct `run(...,pipeline_loader=...)` entry supported; reduce import-stage watchdog from 480 to 180 seconds given successful 90s preflight; update actual subprocess parent-EOF regression fixture so its isolation test continues to exercise owner loss after changed launch order; add a true fail-closed phase-order/failed-import test, update stage/stack CI contracts. No downloading models, disabling review controls, stopping other user processes or touching main.
- **Verification for this repaired code**: source SHA GitHub mirrors and targeted static contract audit only; *full source checkout regression and Windows Qt/CUDA GPU test have NOT run on the repaired head*. Do not claim the old 326/263-module PASS for these new source bytes. Do not request another Windows QA until the complete new-head offline suite passes.
- **Next development action**: run full offline developer unittest suite and source CI from a clean same-HEAD checkout (without GitHub Actions costs); fix actual failures as a group. Then perform one controlled frozen-source Windows RTX 3080 Ti media QA and independently prove at least two distinct reviewer families, including exact installed Qwen2.5:14b only if present. After preliminary PASS, target RTX 2080 Super 8 GiB needs ten consecutive clean runs. Maintain a single Desktop AI_SMM_REPORTS root.

---

# Current integration checkpoint — full regression after 3838bc9

Baseline `3838bc94413a918438aa549d6c99ee2e7cd84e2f`, same draft PR #4 / integration
branch. The commit containing this checkpoint completes shared app-owned
Ollama/vision/Diffusers compute ownership, single-call cold-start contracts,
native stage/lifecycle tests, durable post-inference diagnostics, current-case
Qt progress, auditable VRAM failures and EXIF-consistent mask provenance.

Final runtime fingerprint:
`5e247e039c11904d6a3a9d06de72d1e80bb3d8b78279150093a03095da0cebcc`.
All 263 developer modules **2109 + 1048 PASS**; actual four-shard isolated QA
layout **2109 + 1047 PASS**; complete CI and forced-GC CI **326 + 77 PASS,
2 native-Windows-only skips**. Actual cached tiny CPU Qt image/video generation,
stop/owner-death/same-ID retry/SQLite reopen/receipt adoption PASS. Mirrors and
dependency checks PASS (163 runtime modules). No live Windows, CUDA, full-size
model quality or current Ollama admission is inferred from those results.

[Source bytes, defects, exact commands and complete evidence](evidence/20261109-3838bc9-integrated-regression).
Next physical step: current source **RUN_WINDOWS_QA.cmd → 1**, RTX 3080 Ti,
reports only **%USERPROFILE%\Desktop\AI_SMM_REPORTS**; preserve any existing
junction/history. Live text model admission must be rerun, including the exact
installed Qwen 2.5 14B tag if selected by the verified local roster. Windows
Qt/CUDA acceptance remains **0/10**. No main changes, paid calls, Actions,
model downloads, user builds, release or merge. Continue from new Windows
evidence rather than repeating completed cloud integration.

---

# QA remediation checkpoint — 2026-11-09, pre-Windows acceptance

Source baseline: `af2532124a4bbafb58952226dd1f2a2e982ec8bd`, new integration PR #4 head before checkpoint: `cd9a6eb024f055d26efdbb9e61cc8b6bfdebb9dc` (draft). No merge, main change, paid API, Actions invocation, model download or user release.

**Windows evidence:** Last actual preliminary RTX 3080 Ti source QA ended BLOCKED, exit code 2, 0/10 target clean Windows cycles. Prior report: unit tests 2065 PASS/1 unprivileged symlink creation FAIL; CI 303 PASS, 2 skips, 77 subtests PASS; text reviewer six candidates unqualified (five timeout, Qwen2.5-coder 7B 3/6); first instruct_pix2pix child manually terminated after stall. These results are for the old source snapshot and do NOT certify this revision.

**Implemented across the same branch, three Work/Developer/testing mirrors where applicable:** unprivileged junction fallback for the real linked-ancestor protection test; 180-second local reviewer call ceiling with unchanged six-control oracle, no cloud fallback; genuine subprocess stage markers, device-lock and per-stage watchdog budgets, bounded durable error telemetry before TemporaryDirectory cleanup; real model-stage observation in active_case.json; 3GiB free VRAM gate using actual nvidia-smi measurement before each Qt generative case (90s bounded wait, no termination of other users' processes); elimination of redundant per-model Ollama generate/unload calls before CUDA; deterministic separate actual binary PNG inpainting QA masks with provenance and hash-protected DIAGNOSTICS.zip inclusion; one user-facing Desktop AI_SMM_REPORTS destination and added source CI contracts.

**Tests actually executed in this ChatGPT tool container:** 11/11 PASS isolated copied pure helpers and QA mask tests (Pillow/Python on Linux). These are *not* the original repository's full regression, nor a source-snapshot-linked Windows test. GitHub source tree audit: six changed production modules identical in Work, Developer and Developer/tests; all 162 shared modules have no SHA divergence. CI tests created on the PR have NOT been run in the full original checkout. No clean-release or Windows qualification is claimed.

**Next objective:** Run a complete *offline* regression on a full same-HEAD checkout before any further Windows run; integrate and correct real failures as a group. After that, run independent frozen-source Windows Qt/CUDA preliminary QA on RTX 3080 Ti and the mandatory consecutive 10 clean target Windows E2E runs on RTX 2080 Super. Verify Qwen 2.5 14B's exact Ollama local tag from /api/tags, and preserve other model independence/quality gates. Do not ask the user to run a stale source snapshot. Desktop AI_SMM_REPORTS currently may be a junction to historical AppData QA; do not move live or historical user evidence without independently verified safe migration.

---

# Current checkpoint — first real Windows QA fixes (2026-11-09)

Existing integration branch / draft PR #4, baseline `8f48fad`. One linked source
phase fixes Windows media identity (CPython 3.12 ctime semantics), full CI/test
isolation, SQLite ownership, fixed reviewer-panel call boundaries, UTF-8 wire
evidence and source snapshot packaging authorities. Security checks and release
criteria remain enforced. Runtime fingerprint:
`8ac7247af0f18cc0ad986b8b58e3ac25545b97ab4154bf7494de215f7118949c`.

Available Linux regression: all 263 developer modules **2109 + 1047 PASS**;
actual QA four-shard isolated layout **2109 + 1046 PASS**; complete CI and forced
GC **303 + 77 PASS, 2 Windows-only skips**. Actual cached tiny CPU Qt image/video
inference, cancel/owner-crash/same-ID retry/SQLite receipt recovery PASS. This is
not Windows/CUDA or media quality acceptance. Six archived reviewer replies
still FAIL under unchanged validators; live requalification awaits the PC.

[Causes, exact commands, logs, byte manifests and limits](evidence/20261109-first-windows-qa-fixes).
Next: update this branch on RTX 3080 Ti, run **RUN_WINDOWS_QA.cmd → 1**, return its
new session DIAGNOSTICS.zip from `%LOCALAPPDATA%\AI_SMM_Manager\QA`.
No model download, paid calls, Actions, user build, release or merge. Windows
final acceptance remains **0/10**. Do not repeat completed cloud integration;
continue from new actual Windows evidence and preserve failed model verdicts.

---

# Current development state — one-launch two-PC Windows QA

Existing integration branch / draft PR #4. User supplies two LAN Windows PCs,
RTX 3080 Ti first and RTX 2080 Super 8 GB final, with no direct Work access.
Baseline `c49fbd0`; tested implementation `36f5decbec04918d51d3f237169515f24fdc2049`,
following `7fb73b5`. Main untouched; no build/release, merge, paid calls or Actions.
No models downloaded in this session; existing tiny CPU fixtures were reused.

**Concrete user launch:** `RUN_WINDOWS_QA.cmd` at repository root. Choose **1**
on RTX 3080 Ti; after a clean full pass copy `PRELIMINARY_ATTESTATION.json` by LAN.
Choose **2** on RTX 2080 Super for **10 consecutive clean full source Qt E2E**.
Failure/uncertainty/drift/replay/live descendants reset to zero. Both actual
counts remain **0** until physically executed. No Work/Linux evidence advances them.

One report root: `%LOCALAPPDATA%\AI_SMM_Manager\QA`. Includes bootstrap console,
HOST/prerequisites, persistent setup, STATUS/LATEST, per-pass source copies,
raw logs/JUnit, all retained QA projects/SQLite/exports, screenshots, model and
output receipts, VRAM samples, metrics/local vision observations and SHA manifests.
[Exact first-launch guide and honest limitations](WINDOWS_ONE_CLICK_QA.md).
Missing dependencies/models/input/reviewer yield BLOCKED; no automatic package/
model download, cloud fallback, report upload or permission to spend is implied.

- Distinct actual GPU roles; clean frozen source/data each run; portable primary
  attestation bound to source, fixtures, model/manifest and dependency identity.
- Full old text/Qt/independent review and developer suite plus complete offline
  CI; all four actual CUDA generative engines and qualified installed local
  vision quality review. Metrics include change/contrast, exact mask protection,
  video shape/duration/flicker samples and decoded interval audio correlation.
- Torch allocated/reserved peaks, strict 8 GB envelope, real nvidia-smi sampling;
  missing/N/A memory evidence cannot pass. Local-only allowlist/credential/proxy
  isolation forbids paid/external transports, even with inherited API keys.
- Actual Qt model child stop, owner crash/EOF exit, stable-ID retry, independent
  SQLite integrity/hash reopening and adoption without re-inference. Win32
  suspended child/owned kill-on-close Job Object contains only QA descendants.
- Native codec failures retain bounded stderr/exit code; codec/memory preflight
  precedes long tests; all failed/partial quality diagnostics are retained.
- Source QA explicitly does **not** fabricate a packaging/release PASS. The old
  packaged-candidate acceptance and independent/final delivery gates remain.

Final Work verification on fingerprint
`d060bce030f0ad78f7e6333718d91039db789b1d2561c2f7a1ecf0c59ad3d283`:
full developer **2109 + 1046 PASS**, all 263 modules; CI **294 + 77 PASS**,
also with forced GC. Actual tiny CPU neural/Qt stop-owner-crash-retry-SQLite-
receipt lifecycle and Linux Qt navigation/two-project/PDF child PASS. Three
mirrors (161 runtime modules), dependency boundaries and diff checks PASS.
[Commands, failed iterations, final manifests and logs](evidence/20261108-windows-one-click-qa).
No real Windows Job Object, target CUDA, trained media quality or live vision
review was executed here. Pixel controls/model judgement are not an infallible
artistic oracle; diagnostics retain images/video and reasoning for inspection.

**Next:** user runs phase 1 on RTX 3080 Ti using this source and already installed
models/fixtures, provides the session folder for actual platform/model fixes;
then same frozen bytes and phase 2 on RTX 2080 Super. No further request for
remote Windows access is necessary. Do not repeat completed cloud integration
or claim readiness before actual acceptance. Paid review remains disabled.

---

# Previous development state — offline generative image/video engines

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
