from core._llm import DEFAULT_MODEL, get_client
from core.models import Plan, ReviewResult, TestResults

SYSTEM_PROMPT = """You are the code-review step of an automated coding agent. You do not have access \
to the repository -- you only see the diff that was generated, the Plan it was meant to satisfy, and \
the result of running the test suite. Check:
- Correctness: does the diff satisfy every acceptance criterion and handle every edge case in the Plan?
- Test adequacy: given the test results, do the tests plausibly exercise the acceptance criteria and \
edge cases?
- Obvious bugs or regressions visible directly in the diff (wrong operator, unhandled exception, \
inverted logic, off-by-one).
- Scope: does the diff make unrelated changes beyond what the Plan calls for?
Only comment on lines actually present in the diff -- never invent issues about code you cannot see. \
Set status "changes_requested" if any issue would block a human from merging; otherwise "approved". \
Every issue needs a real file (and a line number where determinable from the diff) plus a concrete \
suggested_fix."""


def review(diff: str, plan: Plan, test_results: TestResults) -> ReviewResult:
    user_prompt = f"""Plan summary: {plan.summary}

Acceptance criteria:
{chr(10).join(f"- {c}" for c in plan.acceptance_criteria)}

Edge cases:
{chr(10).join(f"- {c}" for c in plan.edge_cases)}

Test results: passed={test_results.passed} coverage={test_results.coverage_pct}
{test_results.output}

Diff:
{diff}
"""

    client = get_client()
    response = client.messages.parse(
        model=DEFAULT_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=ReviewResult,
    )
    return response.parsed_output
