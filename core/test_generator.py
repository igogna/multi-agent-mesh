from core.models import Plan, FileChange, TestFile


def generate_tests(plan: Plan, file_changes: list[FileChange]) -> list[TestFile]:
    return [
        TestFile(
            path="test_stub_file.py",
            content="# stub test content\n",
        )
    ]
