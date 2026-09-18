"""Per-fact persistence for core/project_intelligence.py's Fact model.
Mirrors tools/state_store.py's shape (one JSON file per entity, anchored to
the project root) but keyed by fact id instead of ticket id, and split
across three git-committed subfolders instead of one gitignored one -- see
README/plan for why: these files are meant to be committed (they surface
Project Intelligence updates in the coder agent's own PR diffs), unlike
.agent_runs/'s run-state, which is disposable and gitignored.
"""

from pathlib import Path
from typing import Literal

from core.project_intelligence import Fact
from tools import config as agentdev_config

_STORE_DIRNAME = ".project-intelligence"
Category = Literal["context", "decisions", "requirements"]

# Spec names 3 folders but Fact.type has 11 values -- this is the resolved
# mapping (see plan doc): decision/requirement get their own folder, every
# other type is "context".
_TYPE_TO_CATEGORY: dict[str, Category] = {
    "decision": "decisions",
    "requirement": "requirements",
}


def category_for_type(fact_type: str) -> Category:
    return _TYPE_TO_CATEGORY.get(fact_type, "context")


def _store_root(project_root: Path | None = None) -> Path:
    root = project_root or agentdev_config.find_project_root() or Path.cwd()
    return root / _STORE_DIRNAME


def _fact_path(fact: Fact, project_root: Path | None = None) -> Path:
    category = category_for_type(fact.type)
    return _store_root(project_root) / category / f"{fact.id}.json"


def save_fact(fact: Fact, project_root: Path | None = None) -> Path:
    path = _fact_path(fact, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fact.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_fact(fact_id: str, project_root: Path | None = None) -> Fact | None:
    """Searches all three category folders since the caller may not know a
    fact's type ahead of time -- symmetric with how few facts exist in
    Phase 1 (no index needed yet)."""
    root = _store_root(project_root)
    for category in ("context", "decisions", "requirements"):
        path = root / category / f"{fact_id}.json"
        if path.is_file():
            return Fact.model_validate_json(path.read_text(encoding="utf-8"))
    return None


def list_facts(category: Category | None = None, project_root: Path | None = None) -> list[Fact]:
    root = _store_root(project_root)
    categories = (category,) if category else ("context", "decisions", "requirements")
    facts = []
    for cat in categories:
        cat_dir = root / cat
        if not cat_dir.is_dir():
            continue
        for path in sorted(cat_dir.glob("*.json")):
            facts.append(Fact.model_validate_json(path.read_text(encoding="utf-8")))
    return facts
