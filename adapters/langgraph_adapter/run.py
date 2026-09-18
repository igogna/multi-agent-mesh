"""LangGraph-orchestrated entry point for the requirement -> plan -> code ->
tests -> review loop (see adapters/langgraph_adapter/graph.py for the graph
itself). Parallel to adapters/cli_adapter/run.py's plain while-loop version --
both share core/ and tools/, including core/routing.py's decision functions
and tools/context_builders.py's context/feedback assembly, so the two stay
behaviorally identical apart from orchestration mechanics and the plan-
approval human gate this adapter adds.

Scope: analyze -> human plan gate -> generate -> generate_tests -> run_tests
-> review only. No GitHub push/PR/secret-scan/human-PR-review here yet -- see
adapters/cli_adapter/run.py::regenerate_and_push for that half, which this
adapter does not attempt to replace.
"""

import argparse
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from adapters.cli_adapter import doctor
from adapters.cli_adapter.run import RequirementError
from adapters.langgraph_adapter.graph import build_graph
from core import _llm
from core.models import RunState
from tools import config


def _print_plan(plan: dict) -> None:
    print(f"\nPlan: {plan['summary']}")
    print(f"Files to touch: {plan['files_to_touch']}")
    print("Acceptance criteria:")
    for c in plan["acceptance_criteria"]:
        print(f"  - {c}")
    print("Edge cases:")
    for c in plan["edge_cases"]:
        print(f"  - {c}")


def _prompt_plan_decision() -> dict:
    answer = input("\nApprove this plan? [y/N]: ").strip().lower()
    if answer == "y":
        return {"decision": "approved"}
    feedback = input("Why not? (fed back to the planner for another attempt): ").strip()
    return {"decision": "rejected", "feedback": feedback}


def _print_outcome(state: RunState) -> None:
    if state.plan_decision == "rejected":
        print("\n=== FAILED after max plan-review rounds -- plan never approved ===")
        print(f"Last plan: {state.plan.summary}")
        print(f"Last rejection feedback: {state.plan_feedback}")
        return

    print(f"\nPlan: {state.plan.summary}")
    print("\nFiles changed:")
    for change in state.file_changes:
        print(f"  {change.path}")

    if state.review_result is None:
        print("\n=== FAILED after max iterations -- tests never passed ===")
        print("\nLast test output:\n" + (state.test_results.output if state.test_results else ""))
        return

    print(
        f"\nTest results: passed={state.test_results.passed} "
        f"coverage={state.test_results.coverage_pct}"
    )
    if state.review_result.status == "approved":
        print("\n=== Review approved ===")
    else:
        print("\n=== FAILED after max iterations -- review still requests changes ===")
        for issue in state.review_result.issues:
            print(f"  {issue.file}: {issue.issue}")


def run(
    requirement: str | None,
    repo_path: str | None,
    base_branch: str,
    ticket_id: str | None = None,
    skip_tests: bool = True,
) -> RunState:
    if not requirement or not requirement.strip():
        raise RequirementError(
            "No requirement detected. Pass --requirement \"...\" describing the change to make "
            "(e.g. --requirement \"Add input validation to the signup form.\")."
        )
    # No fixture fallback here on purpose -- an omitted --repo-path means "this
    # project", resolved the same way `agentdev bootstrap` resolves it.
    repo_path = repo_path or str(config.find_project_root() or Path.cwd())

    settings = config.resolve_settings()
    _llm.set_model(settings.model)

    for result in doctor.run_fast_checks(settings):
        if not result.passed:
            fix_suffix = f" (fix: {result.fix})" if result.fix else ""
            print(f"[doctor] {result.name}: {result.detail}{fix_suffix}")

    graph = build_graph(checkpointer=MemorySaver())
    initial_state = RunState(
        requirement=requirement,
        repo_url=repo_path,
        base_branch=base_branch,
        ticket_id=ticket_id,
        skip_tests=skip_tests,
        max_iterations=settings.max_iterations,
    )
    thread_id = ticket_id or f"graph-run-{time.strftime('%Y%m%d-%H%M%S')}"
    graph_config = {"configurable": {"thread_id": thread_id}}

    result = graph.invoke(initial_state, graph_config)
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        _print_plan(payload["plan"])
        resume = _prompt_plan_decision()
        result = graph.invoke(Command(resume=resume), graph_config)

    final_state = RunState.model_validate(result)
    _print_outcome(final_state)
    return final_state


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Same flags as adapters/cli_adapter/run.py's add_arguments, so the two
    adapters are drop-in equivalents from the CLI's point of view."""
    default_base_branch = config.resolve_settings().base_branch

    parser.add_argument(
        "--requirement",
        default=None,
        help="Natural-language description of the change to make (required)",
    )
    parser.add_argument(
        "--repo-path",
        default=None,
        help="Local path to the repo to read/edit (default: this project's root)",
    )
    parser.add_argument("--base-branch", default=default_base_branch)
    parser.add_argument("--ticket-id", default=None, help="e.g. AD-101 -- used as the graph's thread id")
    parser.add_argument(
        "--skip-tests",
        dest="skip_tests",
        action="store_true",
        default=True,
        help="Skip the Docker/pytest test-and-lint step (default: on)",
    )
    parser.add_argument(
        "--run-tests",
        dest="skip_tests",
        action="store_false",
        help="Re-enable the Docker/pytest test-and-lint retry loop",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentdev graph-run", description="Run the coding-agent loop (LangGraph) against a repo."
    )
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        run(args.requirement, args.repo_path, args.base_branch, args.ticket_id, args.skip_tests)
    except RequirementError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
