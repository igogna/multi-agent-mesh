"""Unit tests for the LangGraph orchestration (adapters/langgraph_adapter/) --
drives the compiled graph through graph.invoke/Command(resume=...) with every
core/* LLM call and sandbox_tools call stubbed out, so these run with no
Anthropic/Docker calls, same spirit as tests/test_cli_adapter_smoke.py's
non-integration tests but exercising the graph wiring itself rather than the
plain while-loop adapter.
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import adapters.langgraph_adapter.nodes as nodes
from adapters.langgraph_adapter.graph import build_graph
from core import code_generator, requirement_analyzer, review_agent, test_generator
from core.models import FileChange, LintResults, Plan, ReviewResult, RunState, TestFile, TestResults
from tools import sandbox_tools


def _plan(summary: str) -> Plan:
    return Plan(summary=summary, files_to_touch=["a.py"], acceptance_criteria=["works"], edge_cases=["none"])


def _initial_state(max_plan_review_rounds: int = 3, skip_tests: bool = True) -> RunState:
    return RunState(
        requirement="add a thing",
        repo_url="/fake/repo",
        base_branch="main",
        skip_tests=skip_tests,
        max_plan_review_rounds=max_plan_review_rounds,
    )


def _patch_context_builders(monkeypatch):
    monkeypatch.setattr(nodes, "build_analysis_context", lambda repo_path, requirement: {"files": []})
    monkeypatch.setattr(
        nodes,
        "build_generation_context",
        lambda repo_path, plan, previous_changes=None: {"files": [], "file_contents": {}},
    )


def _patch_approved_review(monkeypatch):
    monkeypatch.setattr(review_agent, "review", lambda diff, plan, test_results: ReviewResult(status="approved"))


def _patch_generate(monkeypatch, calls: list):
    def fake_generate(plan, repo_context, feedback):
        calls.append(feedback)
        return [FileChange(path="a.py", diff="+x", full_content="x")]

    monkeypatch.setattr(code_generator, "generate", fake_generate)


def test_plan_approved_first_try_reaches_approved_review(monkeypatch):
    _patch_context_builders(monkeypatch)
    analyze_calls = []
    monkeypatch.setattr(
        requirement_analyzer,
        "analyze",
        lambda requirement, repo_context, feedback=None: analyze_calls.append(feedback) or _plan("v0"),
    )
    generate_calls: list = []
    _patch_generate(monkeypatch, generate_calls)
    _patch_approved_review(monkeypatch)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t-approved"}}

    result = graph.invoke(_initial_state(), config)
    assert "__interrupt__" in result
    result = graph.invoke(Command(resume={"decision": "approved"}), config)

    assert "__interrupt__" not in result
    final = RunState.model_validate(result)
    assert final.review_result.status == "approved"
    assert final.plan_review_rounds == 0
    assert analyze_calls == [None]
    assert len(generate_calls) == 1


def test_plan_rejected_once_then_approved_feeds_back_reason(monkeypatch):
    _patch_context_builders(monkeypatch)
    analyze_calls = []

    def fake_analyze(requirement, repo_context, feedback=None):
        analyze_calls.append(feedback)
        return _plan(f"v{len(analyze_calls) - 1}")

    monkeypatch.setattr(requirement_analyzer, "analyze", fake_analyze)
    generate_calls: list = []
    _patch_generate(monkeypatch, generate_calls)
    _patch_approved_review(monkeypatch)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t-rejected-once"}}

    result = graph.invoke(_initial_state(), config)
    assert result["plan"].summary == "v0"

    result = graph.invoke(Command(resume={"decision": "rejected", "feedback": "wrong files"}), config)
    assert "__interrupt__" in result
    assert result["plan"].summary == "v1"  # analyze re-ran

    result = graph.invoke(Command(resume={"decision": "approved"}), config)
    assert "__interrupt__" not in result
    final = RunState.model_validate(result)
    assert final.review_result.status == "approved"
    assert final.plan_review_rounds == 1
    assert analyze_calls == [None, "wrong files"]


def test_plan_rejected_past_budget_escalates_without_generating(monkeypatch):
    _patch_context_builders(monkeypatch)
    monkeypatch.setattr(
        requirement_analyzer, "analyze", lambda requirement, repo_context, feedback=None: _plan("v")
    )
    generate_calls: list = []
    _patch_generate(monkeypatch, generate_calls)
    _patch_approved_review(monkeypatch)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t-rejected-escalate"}}

    result = graph.invoke(_initial_state(max_plan_review_rounds=2), config)
    for _ in range(2):
        result = graph.invoke(Command(resume={"decision": "rejected", "feedback": "no"}), config)

    assert "__interrupt__" not in result
    final = RunState.model_validate(result)
    assert final.plan_decision == "rejected"
    assert final.plan_review_rounds == 2
    assert final.review_result is None
    assert len(generate_calls) == 0  # never reached generate


def test_failing_tests_retry_generate_then_proceeds(monkeypatch):
    _patch_context_builders(monkeypatch)
    monkeypatch.setattr(
        requirement_analyzer, "analyze", lambda requirement, repo_context, feedback=None: _plan("v0")
    )
    generate_calls: list = []
    _patch_generate(monkeypatch, generate_calls)
    monkeypatch.setattr(
        test_generator,
        "generate_tests",
        lambda plan, file_changes: [TestFile(path="test_a.py", content="def test_a(): pass")],
    )
    test_run_calls = []

    def fake_run_tests(repo_path, file_changes, test_files):
        test_run_calls.append(1)
        passed = len(test_run_calls) > 1
        return TestResults(passed=passed, output="boom" if not passed else "ok")

    monkeypatch.setattr(sandbox_tools, "run_tests", fake_run_tests)
    monkeypatch.setattr(sandbox_tools, "run_lint", lambda repo_path, file_changes: LintResults(passed=True, issues=[]))
    _patch_approved_review(monkeypatch)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t-test-retry"}}

    result = graph.invoke(_initial_state(skip_tests=False), config)
    result = graph.invoke(Command(resume={"decision": "approved"}), config)

    assert "__interrupt__" not in result
    final = RunState.model_validate(result)
    assert final.test_results.passed is True
    assert final.review_result.status == "approved"
    assert len(generate_calls) == 2
    assert generate_calls[0] is None
    assert generate_calls[1] is not None and "boom" in generate_calls[1]
    assert final.iteration == 1
