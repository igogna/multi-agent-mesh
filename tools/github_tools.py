from core.models import FileChange, TestFile


def create_branch(repo_url: str, base_branch: str, branch_name: str) -> str:
    return branch_name


def commit_and_push(
    repo_url: str,
    branch_name: str,
    file_changes: list[FileChange],
    test_files: list[TestFile],
    commit_message: str,
) -> None:
    return None


def open_pr(repo_url: str, branch_name: str, base_branch: str, title: str, body: str) -> tuple[int, str]:
    return 0, "https://example.invalid/stub-pr"


def post_review_comment(repo_url: str, pr_number: int, body: str) -> None:
    return None


def get_pr_diff(repo_url: str, pr_number: int) -> str:
    return "stub: dummy diff"
