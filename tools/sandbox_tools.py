from core.models import FileChange, TestFile, TestResults, LintResults


def run_tests(file_changes: list[FileChange], test_files: list[TestFile]) -> TestResults:
    return TestResults(passed=True, output="stub: dummy test output", coverage_pct=None)


def run_lint(file_changes: list[FileChange]) -> LintResults:
    return LintResults(passed=True, issues=[])
