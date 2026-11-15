# Integrated media regression and explicit export sizes

Baseline `3c7863a6c209343a669ed2debd652d51d768eb8b`, same integration branch /
draft PR #4. Result is the commit containing this checkpoint. Tested runtime:
`47bc181b6ecdf365f4c49b3e6b23a3f04cf17c96d5367dcdb04576f29d1ceacd`.
The byte manifest includes runtime mirrors, developer tests and root/CI helpers.

## Scope and integration decisions

The user requested continuing the existing program and selectively using useful
ideas from their QWEN archive. That archive was reviewed as data; its scripts,
GUI, models and APIs were not launched. Its generation quality is user-rated
3/10, not an accepted backend qualification. No wholesale app replacement,
keyword-intent router, simulated task completion or simplified scoring gate
was imported. No original photographs, user databases or archive logs are
published in this evidence.

One useful idea was reimplemented in the existing architecture: explicit output
sizes (9:16 1080×1920, 1:1 1080×1080, 16:9 1920×1080, 4:5 1080×1350). The Qt
button fills the actual width/height fields. It does not create another job
runner, change duration/style/sources, start a provider or bypass generation
limits. The existing immutable request stores the dimensions; original project
ownership, SQLite, audio preservation, history, cancellation and receipt reuse
remain authoritative. The new control is unavailable while a worker owns an
operation. A real Qt/FFmpeg integration test exports a short synthetic video
with audio to 1080×1080, reopens it through native probing, verifies untouched
source bytes and repeats the same completed task without rendering again.
A negative cross-boundary test rejects these export sizes as oversized CUDA
image-generation requests rather than bypassing the existing model envelope.

## Connected defects found on the actual current checkout

Full baseline CI: 569 passed / 4 failed / 2 native-Windows skips / 86 subtests.
Full baseline developer suite: 2094 passed / 16 failed / 1060 subtests, across
all 263 modules. Earlier isolated subset results did not certify this head.

1. Completion integrity had been added before structured decoding but its
   failure escaped as a generic transport exception. Truncated/unfinished
   Ollama responses now preserve content-free StructuredResponseError evidence,
   remain terminal, and cannot negotiate another route or become a draft/PASS.
   Done/stop requirements and the original bounded attempt policy are unchanged.
2. Bounded streaming exposed obsolete fixtures: an endless fake body, a
   no-size read method, success responses without an affirmative completion,
   and a set substituted for the now-bounded OrderedDict negotiation cache.
   Fixtures now behave as real finite byte streams, explicitly mark ONLY their
   expected successful completions, and preserve the cache's actual contract.
   Production memory/deadline/EOF checks were not weakened to accept mocks.
3. Provider errors retained only status/byte metadata, losing prior project
   diagnostic evidence. Local Ollama error evidence is now bounded to 64 KiB
   and five seconds, with project trace redaction; the user-facing exception
   remains limited to 8 KiB. Oversized successful model responses still fail
   their existing byte budgets; cancellation is propagated rather than hidden.
4. Optional upload cancellation introduced check=None into legacy one-argument
   adapters unnecessarily. The upload boundary now supplies the keyword only
   for an actual callback. Explicit cancellation also runs before source-list
   processing/consent diagnostics. Consent and paid-operation fences remain
   mandatory before any external upload or paid dispatch.
5. Two CI negatives tested the wrong boundary: an inner pytest catch let the
   cancellation scope's required final check throw again, and a temporal-verdict
   fixture had incomplete objective metrics and failed before verdict validation.
   The former now proves cancellation is raised by transport (not swallowed
   until scope exit); the latter supplies valid metrics then requires rejection
   of the unsupported temporal PASS. Neither admission criterion was relaxed.

The first new video fixture used '.4', rejected by the actual FFmpeg duration
parser. It was corrected to '0.4'; the complete final suite was rerun. Failed
iterations are not counted as successful acceptance.

## Final evidence (Linux / Python 3.12 / Qt offscreen)

| Check | Result |
| --- | --- |
| All 263 developer modules | 2109 passed + 1062 subtests, no failures/skips |
| Actual isolated QA layout, 66/66/66/65 modules | 2109 passed + 1061 subtests, no failures/skips |
| Complete CI | 576 passed + 94 subtests, 2 native-Windows-only skips |
| Complete CI with forced GC after every case | Same 576 + 94, 2 skips |
| Actual Qt/FFmpeg preset export and same-ID receipt reuse | PASS, source integrity/audio/1080×1080/SQLite verified |
| Actual cached tiny CPU image generation and lifecycle | PASS, stop/owner death/child exit/same-ID retry/SQLite reopen/receipt adoption |
| Actual cached tiny CPU video generation | PASS, original audio retained |
| Three runtime/test mirrors and dependency graph | PASS, 177 modules |
| Byte integrity and diff checks | PASS |

media-panel-linux.png is an actual offscreen Qt render at 1280×900 with synthetic
fixture data. Minimum size measured 984×626; it is not a Windows screenshot.
Tiny-model results establish real neural topology/lifecycle, not trained-model
quality or CUDA performance. Semantic fixtures are not live reviewer admission.
No new Windows/CUDA execution or increase in the target 0/10 counter is claimed.

Commands (python = /workspace/work/venv/bin/python):

```sh
AI_SMM_TEST_ROOT=/workspace/work/pr4-3c7863a/final-dev-data QT_QPA_PLATFORM=offscreen python ci/run_regression.py --shards 1 --index 0 --timeout 3000 --output /workspace/work/pr4-3c7863a/final-regression
AI_SMM_TEST_ROOT=/workspace/work/pr4-3c7863a/frozen-ci-data QT_QPA_PLATFORM=offscreen python -m pytest -q ci --tb=short --junitxml=/workspace/work/pr4-3c7863a/final-ci.xml
python /workspace/work/pr4-3c7863a/run_isolated.py
AI_SMM_TEST_ROOT=/workspace/work/pr4-3c7863a/gc-data QT_QPA_PLATFORM=offscreen python /workspace/work/pr4-3c7863a/run_gc.py
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-profiles.json --profile tiny-topology-only --lifecycle
QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python ci/studio_local_generative_smoke.py --profiles /workspace/work/generative/tiny-video-profiles.json --profile tiny-video-topology-only
python ci/verify_tree.py
git diff --check
```

## Remaining boundary

Available cloud integration/regression is clean on the recorded source. Actual
Windows native handles/Qt/CUDA, trained-media quality (including whether output
meets the user's request), live local reviewer qualification and ten consecutive
clean target RTX 2080 Super runs remain unverified. Improved test coverage and
export convenience do not turn the archive's 3/10 generation into accepted
quality. The existing Windows QA launcher and single Desktop AI_SMM_REPORTS
location remain; no stale source attestation is carried forward.

No main change, Actions, paid service, model download, merge, user build or release.
