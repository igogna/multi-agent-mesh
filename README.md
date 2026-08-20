# coding-review-agent

Automates the requirement → code → tests → PR → review → human-merge-gate lifecycle.

Three-layer architecture, kept strictly separated so the orchestration framework can be swapped later:

- `core/` — framework-agnostic agent logic (plain functions, pydantic data in/out). No orchestration imports allowed.
- `tools/` — framework-agnostic integrations (GitHub, Docker sandbox, repo reading, secret scanning, local git workspace, per-ticket state persistence).
- `adapters/` — the only place orchestration-framework-specific code may live. `cli_adapter` (plain Python loop) today; `langgraph_adapter` later once LangGraph is confirmed.

## Status

The full pipeline is wired up end-to-end:

- `requirement_analyzer` turns a natural-language requirement + file listing into a `Plan` (files to touch, acceptance criteria, edge cases).
- `code_generator` / `test_generator` produce full file rewrites and pytest-style tests against that plan, incorporating feedback from a prior failed attempt when retrying.
- `sandbox_tools` runs tests and `ruff` lint inside a Docker container (`sandbox/Dockerfile`) — generated code never runs on the host. Failures feed back into another code-gen round, up to `max_iterations`.
- `secret_scan` scans the generated diff for likely secrets (AWS/GitHub/Slack/Google keys, generic `key = "..."` assignments) before anything is pushed; a hit is treated like a failed test and triggers a retry.
- `github_tools` (via GitPython + PyGithub) creates the working branch, commits, force-pushes (only ever to agent-owned branches), and opens the PR — reusing an existing open PR for the branch if one exists.
- `review_agent` runs an automated LLM review of the diff against the Plan and posts the verdict as a PR comment. Approval moves to the **human merge gate**; the agent never merges its own PR. Rejections retry code-gen (with the review feedback appended) until `max_iterations`, then escalate for human triage.
- `check_pr` (`adapters/cli_adapter/check_pr.py`) is a second entry point: given a `--ticket-id`, it resumes the persisted `RunState` (`tools/state_store.py`, one JSON file per ticket under `.agent_runs/`), makes a single cheap GitHub read of the PR's real review state (`github_tools.get_pr_status`, filtering out the bot's own comments), and only re-runs code-gen when a human has actually requested changes — never re-deriving the plan or opening a new PR. Bounded by `max_human_review_rounds`; merged/closed PRs and idle PRs are recognized and left alone.
- GitHub integration is optional: if `GITHUB_TOKEN`/`GITHUB_REPO` aren't set (or are still placeholders), `run.py` does the full analyze → code → test loop and stops — no branch, PR, or review step.

Still open: no orchestration framework yet — `cli_adapter` is a plain Python loop; `langgraph_adapter` is Phase 6+.

## Setup

1. `pip install -r requirements.txt`
2. Create a `.env` in the repo root (gitignored) with:
   ```
   ANTHROPIC_API_KEY=<your key from console.anthropic.com>

   # Optional -- omit to run analyze/code/test only, with no GitHub PR/review phase
   GITHUB_TOKEN=<a token with repo access>
   GITHUB_REPO=<owner/repo>
   GITHUB_BASE_BRANCH=main
   ```
3. Start Docker Desktop. `tools/sandbox_tools.py` builds a sandbox image on first use and runs every test/lint invocation inside a container.
4. Run the CLI adapter against the toy repo:
   ```
   python -m adapters.cli_adapter.run
   ```
   Override `--requirement` / `--repo-path` / `--base-branch` to target a different requirement or repo, and pass `--ticket-id AD-101` to get a `feature/AD-101` branch name and persist `RunState` to `.agent_runs/AD-101.json` (needed for `check_pr` below) instead of the one-off `run_output.json`.
5. Once a human has reviewed the opened PR, check for actionable feedback and push a revision if needed:
   ```
   python -m adapters.cli_adapter.check_pr --ticket-id AD-101
   ```
   Run this again any time to re-check; there's no poll loop.

## Tests

`pytest` runs the fast unit suite only — model smoke tests, `core/routing.py` decision branches, `tools/repo_context.py`, `tools/secret_scan.py` pattern matching, `core/pr_description.py` body formatting, and `tools/github_tools.py` against mocked GitHub/git clients. No Docker or API key required.

The full end-to-end loop lives in `tests/test_cli_adapter_smoke.py`, marked `integration` and excluded by default (see `pytest.ini`):
- `test_cli_adapter_runs_end_to_end` needs Docker running and a real `ANTHROPIC_API_KEY` — run with `pytest -m integration`.
- `test_cli_adapter_opens_pr_end_to_end` additionally needs real `GITHUB_TOKEN`/`GITHUB_REPO` pointing at a disposable test repo, and is skipped automatically otherwise.

## Full setup instructions

TODO — filled in once the `langgraph_adapter` is built (Phase 6+).
