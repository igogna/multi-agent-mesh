"""Append-only, cross-run activity log at ~/.local/share/agentdev/runs.jsonl --
distinct from tools/state_store.py's RunState persistence, which is a single
JSON file overwritten on every save and carries no timestamps, so nothing is
countable across runs from it alone. One JSON line per completed `agentdev
run`. No secrets: only ids, counts, and an outcome label.
"""

import json
import time
from pathlib import Path

LOG_PATH = Path.home() / ".local" / "share" / "agentdev" / "runs.jsonl"


def append(entry: dict) -> Path:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    return LOG_PATH


def append_run(
    *,
    ticket_id: str | None,
    repo: str | None,
    dev_login: str | None,
    iterations: int,
    human_review_rounds: int,
    outcome: str,
    agentdev_version: str,
) -> Path:
    return append(
        {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ticket_id": ticket_id,
            "repo": repo,
            "dev_login": dev_login,
            "iterations": iterations,
            "human_review_rounds": human_review_rounds,
            "outcome": outcome,
            "agentdev_version": agentdev_version,
        }
    )


def append_bootstrap(
    *,
    repo: str | None,
    nodes_indexed: int,
    edges_indexed: int,
    docs_ingested: int,
    doc_extraction_tokens: int,
    facts_created: int,
) -> Path:
    """Phase-1-scoped subset of the spec's full token/efficiency tracking --
    only what agentdev bootstrap actually produces today (no ContextRouter/
    ToTPlanner yet, so their token categories don't apply). See the plan
    doc's "Token/efficiency tracking" section."""
    return append(
        {
            "event": "bootstrap",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "repo": repo,
            "nodes_indexed": nodes_indexed,
            "edges_indexed": edges_indexed,
            "docs_ingested": docs_ingested,
            "doc_extraction_tokens": doc_extraction_tokens,
            "facts_created": facts_created,
        }
    )
