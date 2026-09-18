from core._llm import get_client, get_model
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
    # Streaming + thinking disabled: see core/code_generator.py for why --
    # reviewing a large multi-file diff can need more than a few thousand
    # output tokens for the issues list, and without this the model's share
    # of the budget spent on invisible reasoning varies run to run, so the
    # same call can truncate unpredictably even when an earlier one didn't.
    with client.messages.stream(
        model=get_model(),
        max_tokens=16000,
        thinking={"type": "disabled"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=ReviewResult,
    ) as stream:
        response = stream.get_final_message()
    return response.parsed_output
