from core.models import Plan, FileChange


def generate(plan: Plan, repo_context: dict, feedback: str | None) -> list[FileChange]:
    return [
        FileChange(
            path="stub_file.py",
            diff="stub: dummy diff",
            full_content="# stub content\n",
        )
    ]
