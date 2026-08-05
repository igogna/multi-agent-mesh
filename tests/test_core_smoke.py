from core.models import (
    Plan,
    FileChange,
    TestFile,
    TestResults,
    LintResults,
    ReviewIssue,
    ReviewResult,
    RunState,
)


def test_models_instantiate():
    plan = Plan(summary="s", files_to_touch=["a.py"], acceptance_criteria=["c"], edge_cases=["e"])
    file_change = FileChange(path="a.py", diff="d", full_content="content")
    test_file = TestFile(path="test_a.py", content="content")
    test_results = TestResults(passed=True, output="ok")
    lint_results = LintResults(passed=True, issues=[])
    review_issue = ReviewIssue(file="a.py", line=1, issue="i", suggested_fix="f")
    review_result = ReviewResult(status="approved", issues=[review_issue])
    state = RunState(requirement="req", repo_url="url", base_branch="main")

    assert plan.summary == "s"
    assert file_change.path == "a.py"
    assert test_file.path == "test_a.py"
    assert test_results.passed is True
    assert lint_results.passed is True
    assert review_result.status == "approved"
    assert state.iteration == 0
    assert state.max_iterations == 3
