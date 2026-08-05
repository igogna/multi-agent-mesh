from core.models import Plan, TestResults, ReviewResult


def review(diff: str, plan: Plan, test_results: TestResults) -> ReviewResult:
    return ReviewResult(status="approved", issues=[])
