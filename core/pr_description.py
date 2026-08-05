from core.models import Plan, FileChange, TestResults


def build_pr_body(plan: Plan, file_changes: list[FileChange], test_results: TestResults) -> str:
    return "# stub PR body\n\nDummy PR description content."
