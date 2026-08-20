from core.models import FileChange, Plan, TestResults
from core.pr_description import build_pr_body


def _plan() -> Plan:
    return Plan(
        summary="Fix divide-by-zero",
        files_to_touch=["calculator/ops.py"],
        acceptance_criteria=["divide(a, 0) raises ValueError"],
        edge_cases=["divide(0, 0)"],
    )


def test_body_includes_summary_files_and_test_results():
    plan = _plan()
    file_changes = [FileChange(path="calculator/ops.py", diff="--- a\n+++ b\n", full_content="...")]
    test_results = TestResults(passed=True, output="1 passed", coverage_pct=87.5)

    body = build_pr_body(plan, file_changes, test_results)

    assert "Fix divide-by-zero" in body
    assert "calculator/ops.py" in body
    assert "divide(a, 0) raises ValueError" in body
    assert "Passed: True" in body
    assert "87.5%" in body


def test_body_handles_missing_coverage_and_no_files():
    plan = _plan()
    test_results = TestResults(passed=False, output="1 failed", coverage_pct=None)

    body = build_pr_body(plan, [], test_results)

    assert "n/a" in body
    assert "Passed: False" in body
