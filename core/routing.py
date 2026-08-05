from typing import Literal

from core.models import RunState


def decide_after_tests(state: RunState) -> Literal["retry", "proceed", "escalate"]:
    return "proceed"


def decide_after_review(state: RunState) -> Literal["merge_gate", "retry", "escalate"]:
    return "merge_gate"
