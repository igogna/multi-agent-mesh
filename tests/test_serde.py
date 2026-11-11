import os
import pytest
from core.models import (
    FileChange,
    HumanReviewIssue,
    HumanReviewResult,
    LintResults,
    Plan,
    ReviewIssue,
    ReviewResult,
    RunState,
    TestFile,
    TestResults,
)
from adapters.langgraph_adapter.serde import ALLOWED_MSGPACK_MODELS, get_checkpoint_serde


def test_all_core_models_in_allowed_msgpack_models():
    expected_models = {
        FileChange,
        HumanReviewIssue,
        HumanReviewResult,
        LintResults,
        Plan,
        ReviewIssue,
        ReviewResult,
        RunState,
        TestFile,
        TestResults,
    }
    allowed_set = set(ALLOWED_MSGPACK_MODELS)
    assert expected_models.issubset(allowed_set)


def test_roundtrip_serialization_under_strict_mode(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")

    serde = get_checkpoint_serde()

    plan = Plan(
        summary="Test Plan",
        files_to_touch=["core/models.py"],
        acceptance_criteria=["Works"],
        edge_cases=["None"],
    )
    fc = FileChange(path="core/models.py", diff="diff content", full_content="full content")
    lint = LintResults(passed=True, issues=[])
    test_res = TestResults(passed=True, output="10 passed", coverage_pct=95.0)
    rev = ReviewResult(status="approved", issues=[ReviewIssue(file="a.py", line=1, issue="none", suggested_fix="none")])

    state = RunState(
        requirement="test req",
        repo_url="https://github.com/example/repo",
        base_branch="main",
        plan=plan,
        file_changes=[fc],
        lint_results=lint,
        test_results=test_res,
        review_result=rev,
    )

    data = serde.dumps_typed(state)
    loaded_state = serde.loads_typed(data)

    assert loaded_state.requirement == "test req"
    assert loaded_state.plan.summary == "Test Plan"
    assert loaded_state.file_changes[0].path == "core/models.py"
    assert loaded_state.lint_results.passed is True
    assert loaded_state.test_results.coverage_pct == 95.0
    assert loaded_state.review_result.status == "approved"

