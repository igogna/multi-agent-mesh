import difflib

from pydantic import BaseModel

from core._llm import get_client, get_model
from core.models import FileChange, Plan

SYSTEM_PROMPT = """You are the code-generation step of an automated coding agent. Given a Plan and the \
current content of the files it names, write the full new content of every file that needs to change \
to satisfy the plan's acceptance criteria and edge cases. Always return the COMPLETE new content of \
each file, not a diff or a snippet. If you are given feedback from a failed test run or a prior review, \
fix exactly what it describes."""


class _FileChangeDraft(BaseModel):
    path: str
    full_content: str


class _FileChangesOutput(BaseModel):
    changes: list[_FileChangeDraft]


def generate(plan: Plan, repo_context: dict, feedback: str | None) -> list[FileChange]:
    file_contents = repo_context.get("file_contents", {})

    existing_files_block = "\n\n".join(
        f"### {path}\n```\n{content}\n```" for path, content in file_contents.items()
    ) or "(none of the target files exist yet)"

    feedback_block = f"\n\nFeedback from the previous attempt (fix this):\n{feedback}" if feedback else ""

    user_prompt = f"""Plan summary: {plan.summary}

Files to touch: {", ".join(plan.files_to_touch)}

Acceptance criteria:
{chr(10).join(f"- {c}" for c in plan.acceptance_criteria)}

Edge cases:
{chr(10).join(f"- {c}" for c in plan.edge_cases)}

Current content of the target files:
{existing_files_block}{feedback_block}
"""

    client = get_client()
    # Streaming, not .parse(): scaffolding several files (e.g. a full Xcode
    # project) can need well over 8192 output tokens, and the SDK requires
    # streaming for calls that may run long enough to need that much budget.
    with client.messages.stream(
        model=get_model(),
        max_tokens=64000,
        thinking={"type": "disabled"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=_FileChangesOutput,
    ) as stream:
        response = stream.get_final_message()

    file_changes = []
    for draft in response.parsed_output.changes:
        original = file_contents.get(draft.path, "")
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                draft.full_content.splitlines(keepends=True),
                fromfile=f"a/{draft.path}",
                tofile=f"b/{draft.path}",
            )
        )
        file_changes.append(
            FileChange(path=draft.path, diff=diff, full_content=draft.full_content)
        )
    return file_changes
