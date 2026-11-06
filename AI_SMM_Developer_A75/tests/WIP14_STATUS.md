# WIP14 — architecture validation checkpoint / NOT_RELEASE

Base: permanently preserved WIP13; actual Windows QA failed with 0/3 live AI deliveries and Qt native crash.

Changes:
- Reviewer availability is separated from substantive, verified findings. Previously a primary review transport/control failure could skip the qualified backup.
- A reviewer that failed is excluded from the subsequent mandatory independent substance opinion. A third qualified family must be admitted, or acceptance fails closed.
- Unverified allegations from a partially unavailable review are explicitly quarantined; they do not authorize content rewrite or become host-certified evidence.
- Qt GUI close refuses while any tracked QThread remains actually running, including the gap between `done` and `finished`; Windows QA drains tracked threads before judging completion.
- Fresh-identity regression cases cover transport outages, reviewer exceptions, both reviewers unavailable, partial allegations, and worker cleanup.

Evidence limits: Python regression and archive integrity only. Real Windows GUI, live reviewer quality, real Ollama/OpenAI and native crash behavior on these exact bytes are NOT confirmed. No paid calls were made.
Status: NOT_RELEASE.
