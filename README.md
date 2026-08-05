# coding-review-agent

Automates the requirement → code → tests → PR → review → human-merge-gate lifecycle.

Three-layer architecture, kept strictly separated so the orchestration framework can be swapped later:

- `core/` — framework-agnostic agent logic (plain functions, pydantic data in/out). No orchestration imports allowed.
- `tools/` — framework-agnostic integrations (GitHub, Docker sandbox, repo reading, secret scanning).
- `adapters/` — the only place orchestration-framework-specific code may live. `cli_adapter` (plain Python loop) first; `langgraph_adapter` later once LangGraph is confirmed.

## Status

Phase 1 scaffold: `core/` and `tools/` contain stub functions returning dummy data. No real LLM calls, Docker execution, or GitHub API calls yet.

## Setup (once later phases are implemented)

1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in the values (see comments for which phase needs which key).
3. Run the CLI adapter: `python -m adapters.cli_adapter.run` (not yet functional — Phase 2).

## Full setup instructions

TODO — filled in at Step 7 of the build order, once the system is fully wired up.
