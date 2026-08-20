from core._llm import DEFAULT_MODEL, get_client
from core.models import Plan

SYSTEM_PROMPT = """You are the planning step of an automated coding agent. Given a natural-language \
requirement and a listing of files in the target repository, produce a Plan: which files need to \
change, what acceptance criteria a correct implementation must satisfy, and what edge cases matter. \
The plan is the shared source of truth that both the code-generation step and the review step will be \
graded against, so acceptance criteria must be concrete and independently checkable -- not vibes."""


def analyze(requirement: str, repo_context: dict) -> Plan:
    files = repo_context.get("files", [])
    file_listing = "\n".join(files) if files else "(no files found)"

    user_prompt = f"""Requirement:
{requirement}

Repository files:
{file_listing}
"""

    client = get_client()
    response = client.messages.parse(
        model=DEFAULT_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=Plan,
    )
    return response.parsed_output
