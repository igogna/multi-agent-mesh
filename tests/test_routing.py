import pytest

from core.models import HumanReviewResult, ReviewResult, RunState, TestResults
from core.routing import (
    decide_after_human_review,
    decide_after_plan_review,
    decide_after_review,
    decide_after_tests,
)


def _plan_review_state(
    decision: str | None, plan_review_rounds: int, max_plan_review_rounds: int = 3
) -> RunState:
    return RunState(
        requirement="req",
        repo_url="repo",
        base_branch="main",
        plan_decision=decision,
        plan_review_rounds=plan_review_rounds,
        max_plan_review_rounds=max_plan_review_rounds,
    )


def test_proceed_when_plan_approved():
    assert decide_after_plan_review(_plan_review_state("approved", plan_review_rounds=0)) == "proceed"


def test_retry_when_plan_rejected_and_rounds_remain():
    assert decide_after_plan_review(_plan_review_state("rejected", plan_review_rounds=0)) == "retry"


def test_escalate_when_plan_rejected_and_rounds_exhausted():
    state = _plan_review_state("rejected", plan_review_rounds=3, max_plan_review_rounds=3)
    assert decide_after_plan_review(state) == "escalate"


def test_raises_without_plan_decision():
    state = RunState(requirement="req", repo_url="repo", base_branch="main")
    with pytest.raises(ValueError):
        decide_after_plan_review(state)


def _state(passed: bool, iteration: int, max_iterations: int = 3) -> RunState:
    return RunState(
        requirement="req",
        repo_url="repo",
        base_branch="main",
        test_results=TestResults(passed=passed, output=""),
        iteration=iteration,
        max_iterations=max_iterations,
    )


def _review_state(status: str, iteration: int, max_iterations: int = 3) -> RunState:
    return RunState(
        requirement="req",
        repo_url="repo",
        base_branch="main",
        review_result=ReviewResult(status=status, issues=[]),
        iteration=iteration,
        max_iterations=max_iterations,
    )


def test_proceed_when_tests_pass():
    assert decide_after_tests(_state(passed=True, iteration=0)) == "proceed"


def test_retry_when_tests_fail_and_iterations_remain():
    assert decide_after_tests(_state(passed=False, iteration=0)) == "retry"


def test_escalate_when_tests_fail_and_iterations_exhausted():
    assert decide_after_tests(_state(passed=False, iteration=3, max_iterations=3)) == "escalate"


def test_raises_without_test_results():
    state = RunState(requirement="req", repo_url="repo", base_branch="main")
    with pytest.raises(ValueError):
        decide_after_tests(state)


def test_merge_gate_when_review_approved():
    assert decide_after_review(_review_state("approved", iteration=0)) == "merge_gate"


def test_retry_when_review_requests_changes_and_iterations_remain():
    assert decide_after_review(_review_state("changes_requested", iteration=0)) == "retry"


def test_escalate_when_review_requests_changes_and_iterations_exhausted():
    assert decide_after_review(_review_state("changes_requested", iteration=3, max_iterations=3)) == "escalate"


def test_raises_without_review_result():
    state = RunState(requirement="req", repo_url="repo", base_branch="main")
    with pytest.raises(ValueError):
        decide_after_review(state)


def _human_state(
    human_review: HumanReviewResult,
    human_review_rounds: int = 0,
    max_human_review_rounds: int = 3,
    last_processed_review_id: int | None = None,
) -> RunState:
    return RunState(
        requirement="req",
        repo_url="repo",
        base_branch="main",
        human_review=human_review,
        human_review_rounds=human_review_rounds,
        max_human_review_rounds=max_human_review_rounds,
        last_processed_review_id=last_processed_review_id,
    )


def test_human_review_merged_wins_regardless_of_verdict():
    hr = HumanReviewResult(pr_state="merged", verdict="approved")
    assert decide_after_human_review(_human_state(hr)) == "merged"


def test_human_review_closed_unmerged_is_terminal():
    hr = HumanReviewResult(pr_state="closed_unmerged", verdict="no_review_yet")
    assert decide_after_human_review(_human_state(hr)) == "closed_unmerged"


def test_human_review_regenerate_on_new_changes_requested():
    hr = HumanReviewResult(pr_state="open", verdict="changes_requested", latest_review_id=5)
    assert decide_after_human_review(_human_state(hr)) == "regenerate"


def test_human_review_await_human_when_same_review_already_processed():
    hr = HumanReviewResult(pr_state="open", verdict="changes_requested", latest_review_id=5)
    state = _human_state(hr, last_processed_review_id=5)
    assert decide_after_human_review(state) == "await_human"


def test_human_review_escalate_when_round_budget_exhausted():
    hr = HumanReviewResult(pr_state="open", verdict="changes_requested", latest_review_id=5)
    state = _human_state(hr, human_review_rounds=3, max_human_review_rounds=3)
    assert decide_after_human_review(state) == "escalate"


@pytest.mark.parametrize("verdict", ["approved", "commented_only", "no_review_yet"])
def test_human_review_await_human_for_non_actionable_verdicts(verdict):
    hr = HumanReviewResult(pr_state="open", verdict=verdict)
    assert decide_after_human_review(_human_state(hr)) == "await_human"


def test_raises_without_human_review():
    state = RunState(requirement="req", repo_url="repo", base_branch="main")
    with pytest.raises(ValueError):
        decide_after_human_review(state)
