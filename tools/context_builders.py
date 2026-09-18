"""Pure helpers for assembling LLM inputs and retry feedback. Split out of
adapters/cli_adapter/run.py so both the plain-loop CLI adapter and the
LangGraph adapter (adapters/langgraph_adapter/) share the exact same
context/feedback construction without one importing the other's CLI
internals. Lives in tools/, not core/, because it depends on tools/
(repo_context, project_intelligence_store) for I/O -- core/*.py never imports
tools/, only the reverse."""

from pathlib import Path

from core.context_router import select_relevant_facts
from core.models import FileChange, LintResults, ReviewResult, TestResults
from tools import project_intelligence_store, repo_context


def build_analysis_context(repo_path: str, requirement: str) -> dict:
    context: dict = {"files": repo_context.list_files(repo_path)}
    all_facts = project_intelligence_store.list_facts(project_root=Path(repo_path))
    if all_facts:
        context["project_facts"] = select_relevant_facts(requirement, all_facts)
    return context


def build_generation_context(
    repo_path: str, plan, previous_changes: list[FileChange] | None = None
) -> dict:
    file_contents = {}
    for path in plan.files_to_touch:
        try:
            file_contents[path] = repo_context.read_file(repo_path, path)
        except FileNotFoundError:
            pass
    # Sandbox test/lint runs never write back to repo_path (they materialize a
    # throwaway copy -- see tools/sandbox_tools.py), so without this a retry
    # would only ever see the original, unchanged repo and have to reconstruct
    # the whole plan from scratch based on feedback text alone. Overlaying the
    # prior attempt's own output lets it edit a real draft instead.
    if previous_changes:
        for change in previous_changes:
            file_contents[change.path] = change.full_content
    return {"files": repo_context.list_files(repo_path), "file_contents": file_contents}


def build_feedback(test_results: TestResults, lint_results: LintResults) -> str:
    parts = [f"Test run {'PASSED' if test_results.passed else 'FAILED'}:", test_results.output]
    if not lint_results.passed:
        parts.append("Lint issues:")
        parts.extend(f"- {issue}" for issue in lint_results.issues)
    return "\n".join(parts)


def build_secret_feedback(secrets: list[str]) -> str:
    parts = ["Potential secrets were detected in the generated diff -- remove them before this can proceed:"]
    parts.extend(f"- {issue}" for issue in secrets)
    return "\n".join(parts)


def build_review_feedback(review_result: ReviewResult) -> str:
    parts = ["Code review requested changes:"]
    for issue in review_result.issues:
        location = f"{issue.file}:{issue.line}" if issue.line is not None else issue.file
        parts.append(f"- {location}: {issue.issue} (suggested fix: {issue.suggested_fix})")
    return "\n".join(parts)
