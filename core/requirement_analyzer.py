from core._llm import get_client, get_model
from core.models import Plan
from core.project_intelligence import Fact

SYSTEM_PROMPT = """You are the planning step of an automated coding agent. Given a natural-language \
requirement and a listing of files in the target repository, produce a Plan: which files need to \
change, what acceptance criteria a correct implementation must satisfy, and what edge cases matter. \
The plan is the shared source of truth that both the code-generation step and the review step will be \
graded against, so acceptance criteria must be concrete and independently checkable -- not vibes.

When known facts about the codebase are provided, treat verified facts as ground truth and inferred \
facts as unconfirmed hints -- never plan against a verified fact, and call out in edge_cases if a \
decision depends on an inferred fact that could be wrong."""


def _format_facts_block(facts: list[Fact]) -> str:
    if not facts:
        return ""
    lines = [f"- [{f.status}] {f.content} (evidence: {', '.join(f.evidence) or 'n/a'})" for f in facts]
    return "\n\nKnown facts about this codebase (from prior indexing):\n" + "\n".join(lines)


def analyze(requirement: str, repo_context: dict, feedback: str | None = None) -> Plan:
    files = repo_context.get("files", [])
    file_listing = "\n".join(files) if files else "(no files found)"
    facts_block = _format_facts_block(repo_context.get("project_facts", []))
    feedback_block = (
        f"\n\nA human rejected the previous plan for this reason (address it):\n{feedback}" if feedback else ""
    )

    user_prompt = f"""Requirement:
{requirement}

Repository files:
{file_listing}{facts_block}{feedback_block}
"""

    client = get_client()
    # Streaming, not .parse(), matching code_generator.py/review_agent.py/
    # test_generator.py: a populated facts block gives the model more to
    # reason about, so the plan's acceptance_criteria/edge_cases can run long
    # enough that a non-streaming call risks truncating mid-JSON.
    with client.messages.stream(
        model=get_model(),
        max_tokens=8192,
        thinking={"type": "disabled"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=Plan,
    ) as stream:
        response = stream.get_final_message()
    return response.parsed_output
