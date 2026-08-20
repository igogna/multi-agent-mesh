from typing import Literal

from core.models import RunState


def decide_after_tests(state: RunState) -> Literal["retry", "proceed", "escalate"]:
    if state.test_results is None:
        raise ValueError("decide_after_tests called before test_results is set")
    if state.test_results.passed:
        return "proceed"
    if state.iteration < state.max_iterations:
        return "retry"
    return "escalate"


def decide_after_review(state: RunState) -> Literal["merge_gate", "retry", "escalate"]:
    if state.review_result is None:
        raise ValueError("decide_after_review called before review_result is set")
    if state.review_result.status == "approved":
        return "merge_gate"
    if state.iteration < state.max_iterations:
        return "retry"
    return "escalate"


def decide_after_human_review(
    state: RunState,
) -> Literal["merged", "closed_unmerged", "await_human", "regenerate", "escalate"]:
    if state.human_review is None:
        raise ValueError("decide_after_human_review called before human_review is set")

    hr = state.human_review
    if hr.pr_state == "merged":
        return "merged"
    if hr.pr_state == "closed_unmerged":
        return "closed_unmerged"

    if hr.verdict == "changes_requested":
        if hr.latest_review_id == state.last_processed_review_id:
            return "await_human"
        if state.human_review_rounds >= state.max_human_review_rounds:
            return "escalate"
        return "regenerate"

    return "await_human"
