from pydantic import BaseModel

from core._llm import get_client, get_model
from core.models import FileChange, Plan, TestFile

SYSTEM_PROMPT_TEMPLATE = """You are the test-generation step of an automated coding agent. Given a Plan and the \
new content of the files that were just changed to implement it, write tests that cover the plan's \
acceptance criteria and edge cases -- not just whatever the code happens to do. Write tests using {test_framework_hint}. \
Always return the COMPLETE content of each test file. Prefer adding a new test file over \
editing an existing one unless the plan clearly calls for editing an existing test file."""


class _TestFileDraft(BaseModel):
    path: str
    content: str


class _TestFilesOutput(BaseModel):
    tests: list[_TestFileDraft]


def generate_tests(
    plan: Plan, file_changes: list[FileChange], test_framework_hint: str = "pytest"
) -> list[TestFile]:
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
    # Streaming, not .parse(): a plan touching several/large files can need
    # well over 8192 output tokens, and the SDK requires streaming for calls
    # that may run long enough to need that much budget. thinking is disabled
    # so the whole max_tokens budget goes to the actual test content instead
    # of a variable, self-decided reasoning share (see core/code_generator.py).
    with client.messages.stream(
        model=get_model(),
        max_tokens=64000,
        thinking={"type": "disabled"},
        system=SYSTEM_PROMPT_TEMPLATE.format(test_framework_hint=test_framework_hint),
        messages=[{"role": "user", "content": user_prompt}],
        output_format=_TestFilesOutput,
    ) as stream:
        response = stream.get_final_message()

    return [TestFile(path=t.path, content=t.content) for t in response.parsed_output.tests]
