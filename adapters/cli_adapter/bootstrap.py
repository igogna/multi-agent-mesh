"""`agentdev bootstrap` -- first-time Project Intelligence indexing for an
existing (brownfield) codebase: run Graphify's structural pass, ingest
root-level docs, optionally run a short human Q&A pass for the highest-
impact unknowns, and persist everything as evidence-tagged Facts.

See core/project_intelligence.py for the Fact model and its governance rule,
core/knowledge_extractor.py for how Facts are derived, and
tools/graphify_manager.py / tools/project_intelligence_store.py for the
infra this wires together. Not auto-invoked by `agentdev run` -- indexing
cost/latency should be opt-in and predictable, the same reasoning
`agentdev doctor`'s checks are warn-but-don't-block rather than automatic.
"""

import argparse
from pathlib import Path

from core.knowledge_extractor import HumanAnswer, extract_from_docs, extract_from_graph, seed_from_human_answers
from core.project_intelligence import Fact, FactType
from tools import config, graphify_manager, project_intelligence_store, run_log

_DOC_FILENAMES = ("README.md", "CONTRIBUTING.md")
_DOC_SUBDIRS = ("docs",)

_BOOTSTRAP_QUESTIONS: list[tuple[FactType, str]] = [
    ("constraint", "How does authentication work in this codebase?"),
    ("architecture", "What's the core data model / primary entities?"),
    ("constraint", "How is this deployed (environments, CI/CD)?"),
    ("known_issue", "Any known gotchas or footguns a newcomer should know about?"),
]


def _ensure_gitignored(project_root: Path) -> None:
    gitignore_path = project_root / ".gitignore"
    entry = "graphify-out/"
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.is_file() else ""
    if entry in existing.splitlines():
        return
    suffix = "" if not existing or existing.endswith("\n") else "\n"
    gitignore_path.write_text(existing + suffix + entry + "\n", encoding="utf-8")


def _collect_docs(project_root: Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    for name in _DOC_FILENAMES:
        path = project_root / name
        if path.is_file():
            docs[name] = path.read_text(encoding="utf-8", errors="replace")
    for subdir in _DOC_SUBDIRS:
        dir_path = project_root / subdir
        if dir_path.is_dir():
            for path in sorted(dir_path.rglob("*.md")):
                docs[path.relative_to(project_root).as_posix()] = path.read_text(encoding="utf-8", errors="replace")
    return docs


def _run_interactive_qa() -> list[HumanAnswer]:
    from adapters.cli_adapter import init as init_cmd

    print("\nOptional: seed the highest-impact unknowns (press Enter to skip any).")
    return [
        HumanAnswer(type=fact_type, question=question, answer=init_cmd._prompt(question, ""))
        for fact_type, question in _BOOTSTRAP_QUESTIONS
    ]


def bootstrap(repo_path: str, *, interactive: bool = False) -> list[Fact]:
    project_root = Path(repo_path)
    _ensure_gitignored(project_root)

    if not graphify_manager.is_installed():
        print("Installing graphify ...")
        if not graphify_manager.install():
            raise RuntimeError(
                "failed to install graphify. If this environment has no network access to PyPI, "
                "or ensurepip/pip still isn't available in it (common right after `uv tool "
                "install` on an older build), reinstall with graphify bundled in: "
                "`uv tool install agentdev --with graphifyy --force` (or, if installed via pip, "
                "run `<path to agentdev's python> -m pip install graphifyy`), then retry."
            )

    if graphify_manager.has_existing_graph(repo_path):
        print("Existing graphify-out/graph.json found -- reusing it (run `graphify update .` to refresh).")
        graph_data = graphify_manager.load_graph(repo_path)
    else:
        print("Running Graphify structural index ...")
        graph_data = graphify_manager.run_structural_index(repo_path)

    graph_facts = extract_from_graph(graph_data)
    node_count = len(graph_data.get("nodes", []))
    edge_count = len(graph_data.get("links", []))
    print(f"  {len(graph_facts)} facts from {node_count} nodes / {edge_count} edges")

    docs = _collect_docs(project_root)
    print(f"Ingesting {len(docs)} doc file(s) ...")
    doc_facts, doc_tokens = extract_from_docs(docs)
    print(f"  {len(doc_facts)} facts extracted ({doc_tokens} tokens)")

    human_facts: list[Fact] = []
    if interactive:
        human_facts = seed_from_human_answers(_run_interactive_qa())
        print(f"  {len(human_facts)} facts from human Q&A")

    all_facts = graph_facts + doc_facts + human_facts
    for fact in all_facts:
        project_intelligence_store.save_fact(fact, project_root)

    by_status: dict[str, int] = {}
    for fact in all_facts:
        by_status[fact.status] = by_status.get(fact.status, 0) + 1
    print(f"\nSaved {len(all_facts)} facts to {project_root / '.project-intelligence'}")
    print(f"  by status: {by_status}")

    run_log.append_bootstrap(
        repo=str(project_root),
        nodes_indexed=node_count,
        edges_indexed=edge_count,
        docs_ingested=len(docs),
        doc_extraction_tokens=doc_tokens,
        facts_created=len(all_facts),
    )

    return all_facts


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Also run a short Q&A pass to seed the highest-impact unknowns (auth, data model, deployment, gotchas)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentdev bootstrap", description="First-time Project Intelligence indexing for this repo."
    )
    add_arguments(parser)
    args = parser.parse_args(argv)

    project_root = config.find_project_root()
    if project_root is None:
        print("agentdev bootstrap must be run inside a git repository (no .git found above the current directory).")
        return 1

    bootstrap(str(project_root), interactive=args.interactive)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
