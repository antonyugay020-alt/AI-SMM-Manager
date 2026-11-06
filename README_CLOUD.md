# AI SMM Manager — cloud development workspace (NOT RELEASE)

This is a **development repository** with current application sources, mirrored developer source modules, full regression corpus, GitHub Actions, and an external Windows E2E supervisor. It is **not** an accepted Windows release. Previous native Windows validation failed. No command in this repository silently changes that result.

## Ownership and execution

1. **Codex on an ephemeral GitHub Actions runner** proposes coordinated changes on a new branch through `.github/workflows/codex-development.yml`. It cannot directly update `main`. It cannot edit the QA authority files in that workflow. If Codex cannot run because the API key is missing, status is BLOCKED.
2. **Independent reviewers (Claude and a separately configured OpenAI-compatible provider such as Qwen or DeepSeek)** inspect the pull request diff through `.github/workflows/independent-review.yml`. A missing API key/model, invalid response, or rejected review is BLOCKED. The PR code is **never executed** by the secrets-bearing reviewer job.
3. **Linux and Windows GitHub-hosted regression** runs 4 shards × 2 platforms via `.github/workflows/ci.yml`. The aggregate gate requires evidence from all 8 shards, equal source fingerprints, and complete test module inventory. A suite that times out or lacks a JUnit file is BLOCKED. These tests do not prove quality of live model output.
4. **Packaging** regenerates the acceptance manifest using the application's `release_guard.py` from a fresh staging copy; stale package manifests cannot be reused after source changes. **Native Windows E2E** then runs only on a cloud-hosted **self-hosted** Windows runner labeled `smm-cloud-qa`, with Ollama and an actual graphical desktop. It requires ten clean independent full cycles and valid evidence. Without this external VM, the job stays queued/blocked and the release count is 0/10. GitHub-hosted Windows regression is not equivalent to an interactive GUI E2E test.

## One-time cloud setup needed

The ChatGPT GitHub connector currently lists no accessible repositories and does not expose a `create_repository` action. Therefore the code has been prepared locally in the cloud workspace **but has NOT been pushed to GitHub**.

1. Create a **private** GitHub repository (e.g. `ai-smm-manager`) in your GitHub account; give the connected GitHub app access to it. Import this workspace into its `main` branch (no local PC modification is required; ChatGPT Work or a hosted Git session can do this once the repository is accessible).
2. Configure Actions permissions to allow creating pull requests, and set branch protection on `main`: require the cross-platform regression aggregate gate and both independent reviewer checks, block direct pushes, require human approval for release/CI changes. The included CODEOWNERS is a placeholder until a real GitHub username/team is chosen.
3. In **Settings → Secrets and variables → Actions** add secrets `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ALT_REVIEW_API_KEY`. Configure repository **variables** `ANTHROPIC_MODEL`, `ALT_REVIEW_API_URL` (HTTPS only), `ALT_REVIEW_MODEL`, `AI_SMM_CLOUD_MODELS` (OpenAI/Ollama cloud model tags supported by application). **Do not commit keys**. Credentials already installed on the user's desktop are not automatically present in Actions.
4. Register a **dedicated cloud Windows VM** as a self-hosted runner with labels `windows` and `smm-cloud-qa`. Use an isolated non-production Windows account with visible desktop/interactive session, install Python 3.14, PySide6 dependencies, Ollama, and independent models. Configure model access with provider secrets; never connect the real production project data to tests. The user's PC is NOT the development machine or required QA runner.
5. Manually trigger **AI SMM / Codex architecture branch**, supplying one complete architecture workstream. It produces a draft PR, not a release. Review its code changes and independent reviewer artifacts. Only merge after all required checks pass.
6. Trigger **AI SMM / 10 full Windows E2E cycles** for a release candidate. Keep the status BLOCKED until the actual 10/10 evidence is present on the same frozen source fingerprint.

## Developer commands (run in a cloud runner)

```bash
python ci/verify_tree.py
python ci/run_regression.py --shards 4 --index 0
python ci/run_regression.py --shards 4 --index 1
python ci/run_regression.py --shards 4 --index 2
python ci/run_regression.py --shards 4 --index 3
```

A passing shard is **not** a passing full regression. Windows platform coverage, independent reviews, live model access, native GUI and ten-cycle evidence are additional mandatory checks.

## Security / no false acceptance

- No secrets, real project databases, personal content, or Ollama state is embedded in this repository.
- Codex cannot modify CI scripts, GitHub workflows, or core release guards during automated runs.
- Independent cloud reviews of private source code transmit the PR diff to the configured external providers; enable these only after explicitly choosing the providers and agreeing to their data terms.
- The source/testing mirrors are checked for exact equality before regression.
- Failure to collect required evidence, network errors, missing model credentials, timeout or disabled native GUI test means **BLOCKED**, never PASS.
- Avoid treating the prepared workspace, 4-shard regression, or a simulated model test as a user release.
