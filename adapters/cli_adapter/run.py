"""Plain Python loop wiring core/ + tools/ together -- Phase 2: the real
requirement -> code -> test -> retry loop against a toy repo. Phase 3: once
tests pass, scan the diff for secrets, open a real GitHub PR, run an
automated review, and stop at a human merge gate -- the agent never merges
its own PR. Phase 4: check_pr.py can later resume the persisted state to read
what a human actually did on the PR and, if they requested changes, push a
revision -- without ever re-deriving the plan or opening a new PR. No
orchestration framework yet (that's Phase 6+).
"""

import os
import re
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

from core import code_generator, requirement_analyzer, review_agent, routing, test_generator
from core import pr_description
from core.models import HumanReviewResult, LintResults, Plan, ReviewResult, RunState, TestResults
from tools import github_tools, repo_context, sandbox_tools, secret_scan, state_store

TOY_REPO_PATH = Path(__file__).resolve().parent.parent.parent / "fixtures" / "toy_repo"
DEFAULT_REQUIREMENT = (
    "In calculator/ops.py, make divide(a, b) raise ValueError('cannot divide by zero') "
    "instead of crashing with ZeroDivisionError when b is 0. Normal division must still work."
)


def build_analysis_context(repo_path: str) -> dict:
    return {"files": repo_context.list_files(repo_path)}


def build_generation_context(repo_path: str, plan) -> dict:
    file_contents = {}
    for path in plan.files_to_touch:
        try:
            file_contents[path] = repo_context.read_file(repo_path, path)
        except FileNotFoundError:
            pass
    return {"files": repo_context.list_files(repo_path), "file_contents": file_contents}


def build_feedback(test_results: TestResults, lint_results: LintResults) -> str:
    parts = [f"Test run {'PASSED' if test_results.passed else 'FAILED'}:", test_results.output]
    if not lint_results.passed:
        parts.append("Lint issues:")
        parts.extend(f"- {issue}" for issue in lint_results.issues)
    return "\n".join(parts)


def build_secret_feedback(secrets: list[str]) -> str:
    parts = ["Potential secrets were detected in the generated diff -- remove them before this can proceed:"]
    parts.extend(f"- {issue}" for issue in secrets)
    return "\n".join(parts)


def build_review_feedback(review_result: ReviewResult) -> str:
    parts = ["Code review requested changes:"]
    for issue in review_result.issues:
        location = f"{issue.file}:{issue.line}" if issue.line is not None else issue.file
        parts.append(f"- {location}: {issue.issue} (suggested fix: {issue.suggested_fix})")
    return "\n".join(parts)


def build_review_comment(review_result: ReviewResult, decision: str) -> str:
    header = {
        "merge_gate": "Automated review: approved.",
        "retry": "Automated review: changes requested -- the agent will attempt another revision.",
        "escalate": "Automated review: changes requested -- max iterations reached, needs human triage.",
    }[decision]
    lines = [header]
    for issue in review_result.issues:
        location = f"{issue.file}:{issue.line}" if issue.line is not None else issue.file
        lines.append(f"- **{location}**: {issue.issue} (suggested fix: {issue.suggested_fix})")
    return "\n".join(lines)


def build_human_review_feedback(hr: HumanReviewResult) -> str:
    parts = ["A human reviewer requested changes on the PR:"]
    for item in hr.feedback_items:
        location = f" ({item.path}:{item.line})" if item.path else ""
        parts.append(f"- {item.author}{location}: {item.body}")
    return "\n".join(parts)


def _slugify(text: str, max_words: int = 6) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())[:max_words]
    return "-".join(words) or "change"


def make_branch_name(ticket_id: str | None, requirement: str) -> str:
    if ticket_id:
        return f"feature/{ticket_id}"
    # Pre-ticket-ID fallback, until a PM tool (Jira/ADO/etc.) supplies a real
    # ticket per run -- not the intended long-term naming convention.
    return f"agent/{_slugify(requirement)}-{time.strftime('%Y%m%d-%H%M%S')}"


def build_commit_message(ticket_id: str | None, plan: Plan) -> str:
    return f"{ticket_id}: {plan.summary}" if ticket_id else plan.summary


def build_fixup_commit_message(ticket_id: str | None, round_number: int) -> str:
    prefix = f"{ticket_id}: " if ticket_id else ""
    return f"{prefix}fixup! address review feedback (round {round_number})"


def build_pr_title(ticket_id: str | None, plan: Plan) -> str:
    return f"[{ticket_id}] {plan.summary}" if ticket_id else plan.summary


def build_pr_body_with_ticket(state: RunState) -> str:
    body = pr_description.build_pr_body(state.plan, state.file_changes, state.test_results)
    if state.ticket_id:
        body = f"**Ticket:** {state.ticket_id}\n\n{body}"
    return body


def print_summary(state: RunState, escalated: bool) -> None:
    header = "FAILED after max iterations -- human review needed" if escalated else "Tests passed"
    print(f"\n=== {header} (iteration {state.iteration}) ===")
    print(f"\nPlan: {state.plan.summary}")
    print("\nFiles changed:")
    for change in state.file_changes:
        print(f"  {change.path}")
        preview = "\n".join(change.diff.splitlines()[:15])
        print(preview)
    print("\nTest files added:")
    for test_file in state.test_files:
        print(f"  {test_file.path}")
    print(
        f"\nTest results: passed={state.test_results.passed} "
        f"coverage={state.test_results.coverage_pct}"
    )
    if escalated:
        print("\nLast test output:\n" + state.test_results.output)
    if state.lint_results and not state.lint_results.passed:
        print("\nLint issues:")
        for issue in state.lint_results.issues:
            print(f"  {issue}")


def print_merge_gate_summary(state: RunState) -> None:
    print("\n=== Review approved -- AWAITING HUMAN MERGE ===")
    print(f"PR: {state.pr_url}")
    print("This PR has NOT been merged. A human must review and merge it manually on GitHub.")


def print_review_escalation_summary(state: RunState) -> None:
    print("\n=== FAILED after max iterations -- review still requests changes ===")
    print(f"PR left open for human triage: {state.pr_url}")
    if state.review_result:
        for issue in state.review_result.issues:
            print(f"  {issue.file}: {issue.issue}")


def print_secret_escalation_summary(state: RunState, secrets: list[str]) -> None:
    print("\n=== FAILED after max iterations -- potential secrets in generated code ===")
    print("No branch or PR was created.")
    for issue in secrets:
        print(f"  {issue}")


def write_output(state: RunState) -> None:
    if state.ticket_id:
        path = state_store.save(state)
    else:
        path = Path.cwd() / "run_output.json"
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    print(f"\nFull run state written to {path}")


def regenerate_and_push(
    state: RunState,
    repo_path: str,
    github_repo: str | None,
    github_enabled: bool,
    ticket_id: str | None,
    feedback: str | None,
    run_automated_review: bool,
    commit_message: str | None = None,
) -> Literal["pushed_for_review", "merge_gate", "escalated_tests", "escalated_secrets", "escalated_review"]:
    """One full regen cycle: code_generator -> test_generator -> sandbox
    tests/lint (retrying up to state.max_iterations on failure) -> secret_scan
    -> commit/push. If run_automated_review is True, this also loops on the
    LLM review (review_agent.review + post_review_comment), retrying revisions
    until the review approves or the iteration budget is exhausted, and
    returns 'merge_gate' on approval. If False -- used once a human is already
    engaged with the PR (state.human_has_reviewed) -- the LLM review is
    skipped entirely (it's redundant once a human is the reviewer of record)
    and this returns 'pushed_for_review' right after the push, leaving the
    human to look again on their own schedule. commit_message overrides the
    default build_commit_message(ticket_id, plan) -- used by check_pr to mark
    a human-triggered push as a fixup addressing review feedback, distinct
    from the original commit.

    Raises github_tools.GitHubConfigError if GitHub is enabled but
    misconfigured; callers are responsible for persisting state on that path.
    """
    while True:  # outer: review-retry (only iterates when run_automated_review)
        while True:  # inner: test-retry
            gen_context = build_generation_context(repo_path, state.plan)

            print(f"\n--- Iteration {state.iteration} ---")
            print("Generating code changes ...")
            state.file_changes = code_generator.generate(state.plan, gen_context, feedback)

            print("Generating tests ...")
            state.test_files = test_generator.generate_tests(state.plan, state.file_changes)

            print("Running tests in sandbox ...")
            state.test_results = sandbox_tools.run_tests(repo_path, state.file_changes, state.test_files)

            print("Running lint in sandbox ...")
            state.lint_results = sandbox_tools.run_lint(repo_path, state.file_changes)

            decision = routing.decide_after_tests(state)
            print(f"Decision: {decision}")

            if decision == "proceed":
                break
            if decision == "escalate":
                print_summary(state, escalated=True)
                return "escalated_tests"

            feedback = build_feedback(state.test_results, state.lint_results)
            state.iteration += 1

        print_summary(state, escalated=False)

        if not github_enabled:
            return "pushed_for_review"

        diff = "\n".join(fc.diff for fc in state.file_changes)

        print("Scanning diff for secrets ...")
        secrets = secret_scan.scan(diff)
        if secrets:
            print(f"Decision: secrets found ({len(secrets)})")
            if state.iteration < state.max_iterations:
                feedback = build_secret_feedback(secrets)
                state.iteration += 1
                continue
            print_secret_escalation_summary(state, secrets)
            return "escalated_secrets"

        if state.working_branch is None:
            branch_name = make_branch_name(ticket_id, state.requirement)
            print(f"Creating branch {branch_name} ...")
            github_tools.create_branch(github_repo, state.base_branch, branch_name)
            state.working_branch = branch_name

        print("Committing and pushing changes ...")
        github_tools.commit_and_push(
            github_repo,
            state.working_branch,
            state.file_changes,
            state.test_files,
            commit_message=commit_message or build_commit_message(ticket_id, state.plan),
        )

        if state.pr_number is None:
            print("Opening PR ...")
            state.pr_number, state.pr_url = github_tools.open_pr(
                github_repo,
                state.working_branch,
                state.base_branch,
                title=build_pr_title(ticket_id, state.plan),
                body=build_pr_body_with_ticket(state),
            )
            print(f"PR: {state.pr_url}")

        if not run_automated_review:
            return "pushed_for_review"

        print("Requesting review ...")
        state.review_result = review_agent.review(diff, state.plan, state.test_results)
        review_decision = routing.decide_after_review(state)
        print(f"Review decision: {review_decision}")

        github_tools.post_review_comment(
            github_repo, state.pr_number, build_review_comment(state.review_result, review_decision)
        )

        if review_decision == "merge_gate":
            print_merge_gate_summary(state)
            return "merge_gate"
        if review_decision == "escalate":
            print_review_escalation_summary(state)
            return "escalated_review"

        feedback = build_review_feedback(state.review_result)
        state.iteration += 1
        # Loop back into the outer while -- the inner test loop reruns, and the
        # next push lands as a new commit on the same branch/PR.


def run(requirement: str, repo_path: str, base_branch: str, ticket_id: str | None = None) -> None:
    state = RunState(
        requirement=requirement, repo_url=repo_path, base_branch=base_branch, ticket_id=ticket_id
    )

    print(f"Analyzing requirement against {repo_path} ...")
    state.plan = requirement_analyzer.analyze(requirement, build_analysis_context(repo_path))
    print(f"Plan: {state.plan.summary}")
    print(f"Files to touch: {state.plan.files_to_touch}")

    # NOTE: github_repo is a GitHub "owner/repo" slug, unrelated to state.repo_url
    # (which is the local sandbox path) -- see tools/github_tools.py's module docstring.
    github_repo = os.environ.get("GITHUB_REPO")
    github_enabled = github_tools.is_configured()
    if not github_enabled:
        print("GITHUB_TOKEN/GITHUB_REPO not configured -- skipping GitHub PR/review phase.")

    try:
        regenerate_and_push(
            state, repo_path, github_repo, github_enabled, ticket_id, feedback=None, run_automated_review=True
        )
    except github_tools.GitHubConfigError as exc:
        print(f"GitHub step failed: {exc}")

    write_output(state)


if __name__ == "__main__":
    import argparse

    _base_branch_env = os.environ.get("GITHUB_BASE_BRANCH")
    _default_base_branch = (
        _base_branch_env if _base_branch_env and not _base_branch_env.startswith("<<") else "main"
    )

    parser = argparse.ArgumentParser(description="Run the coding-agent loop against a repo.")
    parser.add_argument("--requirement", default=DEFAULT_REQUIREMENT)
    parser.add_argument("--repo-path", default=str(TOY_REPO_PATH))
    parser.add_argument("--base-branch", default=_default_base_branch)
    parser.add_argument("--ticket-id", default=None, help="e.g. AD-101 -- used for branch/commit/PR naming")
    args = parser.parse_args()

    run(args.requirement, args.repo_path, args.base_branch, args.ticket_id)
