"""Thin adapter to local Graphify (no hosted API, no cloud account, no API
key) for structural code indexing only. Deliberately never invokes
Graphify's semantic/doc extraction (graphify.llm.extract_corpus_parallel) --
that path needs a GEMINI_API_KEY or live subagent dispatch by an interactive
coding-agent session, neither of which fits this project's headless
`agentdev bootstrap` CLI invocation. Doc-derived facts are extracted
separately by core/knowledge_extractor.py::extract_from_docs, using this
project's own Anthropic client instead.
"""

import json
import subprocess
import sys
from pathlib import Path

_GRAPH_OUTPUT_DIRNAME = "graphify-out"
_GRAPH_JSON_FILENAME = "graph.json"


def is_installed() -> bool:
    try:
        import graphify  # noqa: F401

        return True
    except ImportError:
        return False


def install() -> bool:
    """Installs the `graphifyy` package (Graphify's own PyPI name, the same
    one its own skill installs) via pip. Returns whether it's importable
    afterward."""
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "graphifyy"], check=False)
    return is_installed()


def _graph_json_path(repo_path: str) -> Path:
    return Path(repo_path) / _GRAPH_OUTPUT_DIRNAME / _GRAPH_JSON_FILENAME


def has_existing_graph(repo_path: str) -> bool:
    return _graph_json_path(repo_path).is_file()


def load_graph(repo_path: str) -> dict:
    return json.loads(_graph_json_path(repo_path).read_text(encoding="utf-8"))


def run_structural_index(repo_path: str) -> dict:
    """Runs Graphify's AST-only structural pass (no LLM, no API key) and
    writes/refreshes graphify-out/graph.json under repo_path. Returns the
    resulting graph dict -- the same node-link shape as graph.json, with
    nodes under "nodes" and edges under "links" (see
    core/knowledge_extractor.py::extract_from_graph for the fields each
    carries)."""
    if not is_installed():
        raise RuntimeError("graphify is not installed -- call install() first")

    from graphify.build import build_from_json
    from graphify.cluster import cluster
    from graphify.detect import detect
    from graphify.export import to_json
    from graphify.extract import collect_files, extract

    root = Path(repo_path)
    detection = detect(root)

    code_files: list[Path] = []
    for f in detection.get("files", {}).get("code", []):
        path = Path(f)
        code_files.extend(collect_files(path) if path.is_dir() else [path])

    if not code_files:
        raise RuntimeError(f"no code files detected under {repo_path}")

    extraction = extract(code_files, cache_root=root, root=root)
    graph = build_from_json(extraction, root=root, directed=False)
    if graph.number_of_nodes() == 0:
        raise RuntimeError(f"structural extraction produced no nodes for {repo_path}")

    communities = cluster(graph)
    output_path = root / _GRAPH_OUTPUT_DIRNAME / _GRAPH_JSON_FILENAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # No force=True: Graphify's own shrink-guard (refuses to overwrite a
    # larger existing graph.json with a smaller one) is a real safety net,
    # not something to bypass by default -- surface it as an error instead.
    wrote = to_json(graph, communities, str(output_path))
    if not wrote:
        raise RuntimeError(
            f"graphify refused to write {output_path}: the new graph has fewer nodes than the "
            "existing one (its shrink-guard). Investigate before forcing an overwrite."
        )

    return json.loads(output_path.read_text(encoding="utf-8"))
