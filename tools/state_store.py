"""Per-ticket persistence for RunState, so a later, separate check_pr
invocation can resume a run rather than starting from a blank state. Replaces
the single un-keyed run_output.json (a collision risk once multiple tickets
are in flight) with one JSON file per ticket_id.
"""

import os
from pathlib import Path

from core.models import RunState

_DEFAULT_STATE_DIR = Path(".agent_runs")


def _state_dir() -> Path:
    override = os.environ.get("AGENT_STATE_DIR")
    return Path(override) if override else _DEFAULT_STATE_DIR


def _state_path(ticket_id: str) -> Path:
    return _state_dir() / f"{ticket_id}.json"


def save(state: RunState) -> Path:
    if not state.ticket_id:
        raise ValueError("state_store.save requires state.ticket_id to be set")
    path = _state_path(state.ticket_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return path


def load(ticket_id: str) -> RunState | None:
    path = _state_path(ticket_id)
    if not path.exists():
        return None
    return RunState.model_validate_json(path.read_text(encoding="utf-8"))
