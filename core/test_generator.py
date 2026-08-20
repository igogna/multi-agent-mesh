from pydantic import BaseModel

from core._llm import DEFAULT_MODEL, get_client
from core.models import FileChange, Plan, TestFile

SYSTEM_PROMPT = """You are the test-generation step of an automated coding agent. Given a Plan and the \
new content of the files that were just changed to implement it, write tests that cover the plan's \
acceptance criteria and edge cases -- not just whatever the code happens to do. Write pytest-style \
tests. Always return the COMPLETE content of each test file. Prefer adding a new test file over \
editing an existing one unless the plan clearly calls for editing an existing test file."""


class _TestFileDraft(BaseModel):
    path: str
    content: str


class _TestFilesOutput(BaseModel):
    tests: list[_TestFileDraft]


def generate_tests(plan: Plan, file_changes: list[FileChange]) -> list[TestFile]:
    changed_files_block = "\n\n".join(
        f"### {fc.path}\n```\n{fc.full_content}\n```" for fc in file_changes
    ) or "(no files were changed)"

    user_prompt = f"""Plan summary: {plan.summary}

Acceptance criteria:
{chr(10).join(f"- {c}" for c in plan.acceptance_criteria)}

Edge cases:
{chr(10).join(f"- {c}" for c in plan.edge_cases)}

New content of the changed files:
{changed_files_block}
"""

    client = get_client()
    response = client.messages.parse(
        model=DEFAULT_MODEL,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=_TestFilesOutput,
    )

    return [TestFile(path=t.path, content=t.content) for t in response.parsed_output.tests]
