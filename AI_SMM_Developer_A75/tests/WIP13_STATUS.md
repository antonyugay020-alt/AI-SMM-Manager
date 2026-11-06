# AI SMM Manager / WIP13 / NOT_RELEASE

Base: permanently saved WIP12 checkpoint (2026-10-31). No unverified R106 source assumed.

## Architectural change: accurate live acceptance mode
- Full Windows QA now exercises bounded production recovery instead of silently inheriting first-pass-only behavior. Normal application behavior is unchanged.
- Fast QA measures one authored attempt and one permitted bounded final repair; it is not a comprehensive delivery verdict.
- Both profiles use fresh randomized briefs and independent model assignment every invocation, with seed/replay recorded. Existing regression fixtures remain reproducible.
- Full QA preserves task-scoped checkpoints and accepted publication bytes between bounded attempts. It does not approve missing/invalid content or silently downgrade editor requirements.
- Windows QA records the first-pass verdict separately from the final workflow verdict and explicitly reports execution mode and retry budget.
- Explicit contract tests cover the two modes, per-task identity, actual pipeline retry budget, and honest first-pass reporting across freshly randomized inputs.

## Tests and limitations
- Python/offline regression and archive checks are recorded in checkpoint test logs. These do not prove real model reliability.
- No Windows GUI, OpenAI or Ollama live content quality test has run on WIP13. Full profile may expose model timeouts or quality failures; they must be measured, not suppressed.
- Historical unsaved R98-R106 feature completeness remains unverified.

**Status: NOT_RELEASE** until native Windows GUI, real-model delivery, independence, restart and original requirement coverage pass on final bytes.
