"""Node functions for the LangGraph version of the analyze -> human plan gate
-> generate -> generate_tests -> run_tests -> review loop (see
adapters/cli_adapter/run.py::regenerate_and_push for the plain-loop
equivalent this mirrors). Each node takes the current RunState and returns a
dict of fields to merge into it -- LangGraph's contract for a node fn.

Feedback threading: rather than a dedicated "set feedback" node, the step that
produces a result also decides the feedback for a retry of `generate`
(state.feedback), colocated with the step that knows why the retry is
happening -- the same place `regenerate_and_push` computes it today, just
returned instead of assigned to a local variable. `generate_node` only reads
state.feedback and clears it after use.
"""

from langgraph.types import interrupt

from core import code_generator, requirement_analyzer, review_agent, test_generator
from core.models import LintResults, RunState, TestResults
from tools import language_config, repo_context, sandbox_tools
from tools.context_builders import (
    build_analysis_context,
    build_feedback,
    build_generation_context,
    build_review_feedback,
)


def analyze_node(state: RunState) -> dict:
    if not state.requirement or not state.requirement.strip():
        raise ValueError(
            "No requirement detected. Set RunState.requirement before invoking the graph."
        )
    plan = requirement_analyzer.analyze(
        state.requirement,
        build_analysis_context(state.repo_url, state.requirement),
        feedback=state.plan_feedback,
    )
    return {"plan": plan}


def human_plan_gate_node(state: RunState) -> dict:
    # No side effects before interrupt(): on resume, LangGraph re-runs this
    # node from the top, so anything before the interrupt() call would repeat.
    decision = interrupt(
        {
            "type": "plan_approval",
            "plan": state.plan.model_dump(),
        }
    )
    rounds = state.plan_review_rounds if decision["decision"] == "approved" else state.plan_review_rounds + 1
    return {
        "plan_decision": decision["decision"],
        "plan_feedback": decision.get("feedback"),
        "plan_review_rounds": rounds,
    }


def generate_node(state: RunState) -> dict:
    gen_context = build_generation_context(state.repo_url, state.plan, previous_changes=state.file_changes or None)
    new_changes = code_generator.generate(state.plan, gen_context, state.feedback)
    # Merge by path rather than replacing outright, matching
    # regenerate_and_push: once the model can see its prior draft, it may
    # reasonably leave untouched files out of its response.
    changes_by_path = {c.path: c for c in state.file_changes}
    changes_by_path.update({c.path: c for c in new_changes})
    update = {"file_changes": list(changes_by_path.values()), "feedback": None}
    # state.feedback is only ever non-None when this is a retry entry (set by
    # run_tests_node/review_node for exactly that purpose) -- mirrors
    # regenerate_and_push, which increments state.iteration once per retry,
    # right after deciding to retry and before generate() runs again. This
    # keeps decide_after_tests/decide_after_review seeing the pre-increment
    # count, same as the plain loop.
    if state.feedback is not None:
        update["iteration"] = state.iteration + 1
    return update


def generate_tests_node(state: RunState) -> dict:
    if state.skip_tests:
        return {}
    lang = language_config.detect_language(repo_context.list_files(state.repo_url))
    test_files = test_generator.generate_tests(state.plan, state.file_changes, lang.test_framework_hint)
    return {"test_files": test_files}


def run_tests_node(state: RunState) -> dict:
    if state.skip_tests:
        return {
            "test_results": TestResults(passed=True, output="Tests skipped (skip_tests=True)."),
            "lint_results": LintResults(passed=True, issues=[]),
        }

    test_results = sandbox_tools.run_tests(state.repo_url, state.file_changes, state.test_files)
    lint_results = sandbox_tools.run_lint(state.repo_url, state.file_changes)
    update = {"test_results": test_results, "lint_results": lint_results}
    if not test_results.passed:
        update["feedback"] = build_feedback(test_results, lint_results)
    return update


def review_node(state: RunState) -> dict:
    diff = "\n".join(fc.diff for fc in state.file_changes)
    review_result = review_agent.review(diff, state.plan, state.test_results)
    update = {"review_result": review_result}
    if review_result.status == "changes_requested":
        update["feedback"] = build_review_feedback(review_result)
    return update
