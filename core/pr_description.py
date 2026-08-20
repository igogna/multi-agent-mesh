from core.models import FileChange, Plan, TestResults


def build_pr_body(plan: Plan, file_changes: list[FileChange], test_results: TestResults) -> str:
    criteria = "\n".join(f"- [x] {c}" for c in plan.acceptance_criteria) or "(none noted)"
    edge_cases = "\n".join(f"- {c}" for c in plan.edge_cases) or "(none noted)"
    files = "\n".join(f"- `{fc.path}`" for fc in file_changes) or "(no files changed)"
    coverage = f"{test_results.coverage_pct:.1f}%" if test_results.coverage_pct is not None else "n/a"

    return f"""# {plan.summary}

## Acceptance criteria
{criteria}

## Edge cases considered
{edge_cases}

## Files changed
{files}

## Test results
- Passed: {test_results.passed}
- Coverage: {coverage}

---
Opened automatically by the coding agent. A human must review and merge this PR -- it will never be merged automatically."""
