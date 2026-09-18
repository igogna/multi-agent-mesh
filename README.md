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

Install once, globally; configure per project.

1. Install the CLI:
   ```
   uv tool install agentdev
   ```
   (Not published to an index yet -- install from a checkout instead: `uv tool install /path/to/this/repo`,
   or from a built wheel: `uv tool install dist/agentdev-*.whl`.)
2. Inside the project you want to use it on:
   ```
   agentdev init
   ```
   Detects your GitHub remote and its actual default branch, prefers an existing `gh` CLI session over
   asking for a token, validates everything live (repo reachable, branch exists, token has push rights,
   Anthropic key works), and writes `.agentdev.toml` (commit this) plus `~/.config/agentdev/config.toml`
   only if a token fallback is needed (never committed).
3. Set `ANTHROPIC_API_KEY` in your shell environment (get one at console.anthropic.com) -- picked up
   automatically, nothing to configure for it.
4. Index the codebase (once per checkout, and again whenever it feels stale):
   ```
   agentdev bootstrap
   ```
   Runs Graphify's structural pass plus doc ingestion and saves evidence-tagged facts under
   `.project-intelligence/` (gitignored -- derived from your current checkout, so each teammate runs
   this themselves rather than pulling a shared, timestamp-churning snapshot).
5. Run it:
   ```
   agentdev run --ticket-id AD-101 --requirement "Add input validation to the signup form." --repo-path .
   ```
   `--requirement` and `--repo-path` are required — there is no bundled demo/default target, so omitting
   either fails fast with a "No requirement detected" error rather than silently doing nothing useful.
   Start Docker Desktop first only if you want the test/lint retry loop (`--run-tests`); it's skipped by
   default. `agentdev run` checks the fast preflight subset of `agentdev doctor` automatically and warns
   about anything missing before doing real work.
6. Once a human has reviewed the opened PR, check for actionable feedback and push a revision if needed:
   ```
   agentdev check --ticket-id AD-101
   ```
   Run this again any time to re-check; there's no poll loop.

Run `agentdev doctor` any time to diagnose setup problems (git identity, API keys, GitHub auth and push
rights, Docker) with a one-line fix for each. See [docs/USAGE.md](docs/USAGE.md) for the full walkthrough
(flags, config precedence, GitHub auth resolution, troubleshooting) — that's also the doc to hand a
teammate setting this up on their own machine for the first time.

**Legacy comparison path:** `python -m adapters.cli_adapter.run` / `python -m adapters.cli_adapter.check_pr`
still work exactly as before (`.env` + `pip install -r requirements.txt`), kept temporarily so old and new
behavior can be compared side by side during the migration.

## Tests

`pytest` runs the fast unit suite only — model smoke tests, `core/routing.py` decision branches, `tools/repo_context.py`, `tools/secret_scan.py` pattern matching, `core/pr_description.py` body formatting, and `tools/github_tools.py` against mocked GitHub/git clients. No Docker or API key required.

The full end-to-end loop lives in `tests/test_cli_adapter_smoke.py`, marked `integration` and excluded by default (see `pytest.ini`). These make real, costly calls (a real Anthropic call; `test_cli_adapter_opens_pr_end_to_end` opens a real PR), so `-m integration` alone isn't enough to run them -- they also require `AGENTDEV_RUN_INTEGRATION_TESTS=1`, so `pytest -m integration` on reflex can't trigger one by accident:
- `test_cli_adapter_runs_end_to_end` needs Docker running and a real `ANTHROPIC_API_KEY` — run with `AGENTDEV_RUN_INTEGRATION_TESTS=1 pytest -m integration`.
- `test_cli_adapter_opens_pr_end_to_end` additionally needs real GitHub config pointing at a disposable test repo, and is skipped automatically otherwise.

## Full setup instructions

TODO — filled in once the `langgraph_adapter` is built (Phase 6+).
