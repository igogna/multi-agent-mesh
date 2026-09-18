"""Plain Python loop wiring core/ + tools/ together -- Phase 2: the real
requirement -> code -> test -> retry loop against a toy repo. Phase 3: once
tests pass, scan the diff for secrets, open a real GitHub PR, run an
automated review, and stop at a human merge gate -- the agent never merges
its own PR. Phase 4: check_pr.py can later resume the persisted state to read
what a human actually did on the PR and, if they requested changes, push a
revision -- without ever re-deriving the plan or opening a new PR. No
orchestration framework yet (that's Phase 6+).
"""

import argparse
import re
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

from adapters.cli_adapter import doctor
from adapters.cli_adapter.version import version_string
from core import _llm, code_generator, requirement_analyzer, review_agent, routing, test_generator
from core import pr_description
from core.models import (
    FileChange,
    HumanReviewResult,
    LintResults,
    Plan,
    ReviewResult,
    RunState,
    TestResults,
)
from tools import (
    config,
    github_tools,
    language_config,
    repo_context,
    run_log,
    sandbox_tools,
    secret_scan,
    state_store,
)
from tools.context_builders import (
    build_analysis_context,
    build_feedback,
    build_generation_context,
    build_review_feedback,
    build_secret_feedback,
)

class RequirementError(ValueError):
    """Raised when a run is started with no requirement text."""


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


def _print_plan(plan: Plan) -> None:
    print(f"\nPlan: {plan.summary}")
    print(f"Files to touch: {plan.files_to_touch}")
    print("Acceptance criteria:")
    for criterion in plan.acceptance_criteria:
        print(f"  - {criterion}")
    print("Edge cases:")
    for edge_case in plan.edge_cases:
        print(f"  - {edge_case}")


def _prompt_plan_decision() -> tuple[Literal["approved", "rejected"], str | None]:
    answer = input("\nApprove this plan? [y/N]: ").strip().lower()
    if answer == "y":
        return "approved", None
    feedback = input("Why not? (fed back to the planner for another attempt): ").strip()
    return "rejected", feedback


def get_plan_approval(state: RunState, repo_path: str, requirement: str) -> bool:
    """Human plan-approval gate -- every `run()` call goes through this before
    any code is generated, no exceptions. Mirrors
    adapters/langgraph_adapter's human_plan_gate_node + decide_after_plan_review,
    just driven by a plain input() prompt instead of a graph interrupt()."""
    while True:
        _print_plan(state.plan)
        decision, feedback = _prompt_plan_decision()
        state.plan_decision = decision
        if decision == "approved":
            return True
        state.plan_feedback = feedback
        state.plan_review_rounds += 1
        if routing.decide_after_plan_review(state) == "escalate":
            return False
        print("\nRe-planning with your feedback ...")
        state.plan = requirement_analyzer.analyze(
            requirement, build_analysis_context(repo_path, requirement), feedback=feedback
        )


def build_human_review_feedback(hr: HumanReviewResult) -> str:
    parts = ["A human reviewer requested changes on the PR:"]
    for item in hr.feedback_items:
        location = f" ({item.path}:{item.line})" if item.path else ""
        parts.append(f"- {item.author}{location}: {item.body}")
    return "\n".join(parts)


def _slugify(text: str, max_words: int = 6) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())[:max_words]
    return "-".join(words) or "change"


def make_branch_name(ticket_id: str | None, requirement: str, login: str) -> str:
    # login-namespaced so two devs working the same ticket don't force-push
    # over each other's branch (force-push is deliberate in commit_and_push,
    # but it must only ever land on one dev's own branch).
    if ticket_id:
        return f"feature/{login}/{ticket_id}"
    # Pre-ticket-ID fallback, until a PM tool (Jira/ADO/etc.) supplies a real
    # ticket per run -- not the intended long-term naming convention.
    return f"agent/{login}/{_slugify(requirement)}-{time.strftime('%Y%m%d-%H%M%S')}"


def build_commit_message(ticket_id: str | None, plan: Plan) -> str:
    return f"{ticket_id}: {plan.summary}" if ticket_id else plan.summary


def build_fixup_commit_message(ticket_id: str | None, round_number: int) -> str:
    prefix = f"{ticket_id}: " if ticket_id else ""
    return f"{prefix}fixup! address review feedback (round {round_number})"


_PR_TITLE_MAX_LEN = 60


def _shorten(text: str, max_len: int) -> str:
    """Collapses whitespace and truncates at the last word boundary before
    max_len, so PR titles stay readable in GitHub's UI/notifications instead
    of running to a full plan summary's length."""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    truncated = text[: max_len - 1].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated.rstrip(" .,-") + "…"


def build_pr_title(ticket_id: str | None, plan: Plan) -> str:
    if ticket_id:
        prefix = f"[{ticket_id}] "
        return prefix + _shorten(plan.summary, _PR_TITLE_MAX_LEN - len(prefix))
    return _shorten(plan.summary, _PR_TITLE_MAX_LEN)


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
        # Anchored to the project root (see tools/config.find_project_root),
        # not cwd, for the same reason as .agent_runs/ in tools/state_store.py.
        project_root = config.find_project_root() or Path.cwd()
        path = project_root / "run_output.json"
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
    # Detected once per call, not per retry -- a repo's language doesn't
    # change mid-run, and this picks the Docker image/test command/lint
    # command sandbox_tools uses, plus the test-framework hint handed to
    # test_generator (see tools/language_config.py).
    lang = language_config.detect_language(repo_context.list_files(repo_path))

    while True:  # outer: review-retry (only iterates when run_automated_review)
        while True:  # inner: test-retry
            gen_context = build_generation_context(
                repo_path, state.plan, previous_changes=state.file_changes or None
            )

            print(f"\n--- Iteration {state.iteration} ---")
            print("Generating code changes ...")
            new_changes = code_generator.generate(state.plan, gen_context, feedback)
            # Merge by path rather than replacing outright: now that the model
            # can see its prior draft (via build_generation_context's
            # previous_changes overlay), it may reasonably leave untouched
            # files out of its response. A plain replace would silently drop
            # those from the project instead of carrying them forward.
            changes_by_path = {c.path: c for c in state.file_changes}
            changes_by_path.update({c.path: c for c in new_changes})
            state.file_changes = list(changes_by_path.values())

            if state.skip_tests:
                print("Skipping tests/lint (skip_tests=True) ...")
                state.test_results = TestResults(passed=True, output="Tests skipped (skip_tests=True).")
                state.lint_results = LintResults(passed=True, issues=[])
            else:
                print(f"Generating tests ({lang.display_name}) ...")
                state.test_files = test_generator.generate_tests(
                    state.plan, state.file_changes, lang.test_framework_hint
                )

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
            login = github_tools.get_current_login()
            branch_name = make_branch_name(ticket_id, state.requirement, login)
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


def _log_run(state: RunState, settings, github_enabled: bool, outcome: str) -> None:
    """Best-effort: a logging hiccup must never take down a run that
    otherwise completed fine (see tools/run_log.py)."""
    try:
        login = github_tools.get_current_login() if github_enabled else None
    except Exception:
        login = None
    try:
        run_log.append_run(
            ticket_id=state.ticket_id,
            repo=settings.repo,
            dev_login=login,
            iterations=state.iteration,
            human_review_rounds=state.human_review_rounds,
            outcome=outcome,
            agentdev_version=version_string(),
        )
    except OSError as exc:
        print(f"Warning: failed to write run log: {exc}")


def run(
    requirement: str | None,
    repo_path: str | None,
    base_branch: str,
    ticket_id: str | None = None,
    skip_tests: bool = False,
) -> None:
    if not requirement or not requirement.strip():
        raise RequirementError(
            "No requirement detected. Pass --requirement \"...\" describing the change to make "
            "(e.g. --requirement \"Add input validation to the signup form.\")."
        )
    # No fixture fallback here on purpose -- an omitted --repo-path means "this
    # project", resolved the same way `agentdev bootstrap` resolves it.
    repo_path = repo_path or str(config.find_project_root() or Path.cwd())

    settings = config.resolve_settings()
    _llm.set_model(settings.model)

    # Fast preflight only (no Docker check) -- surfaced as warnings, not a hard
    # gate, since GitHub configuration is intentionally optional (see
    # github_enabled below) and skip_tests defaults to True.
    for result in doctor.run_fast_checks(settings):
        if not result.passed:
            fix_suffix = f" (fix: {result.fix})" if result.fix else ""
            print(f"[doctor] {result.name}: {result.detail}{fix_suffix}")

    # Same warn-but-don't-block shape as the doctor checks above: indexing an
    # existing codebase costs time/tokens, so it stays opt-in rather than
    # running implicitly inside every `agentdev run`.
    if not (Path(repo_path) / ".project-intelligence").is_dir():
        print(
            "[bootstrap] No .project-intelligence/ found -- run `agentdev bootstrap` first "
            "for better results on an existing codebase."
        )

    state = RunState(
        requirement=requirement,
        repo_url=repo_path,
        base_branch=base_branch,
        ticket_id=ticket_id,
        skip_tests=skip_tests,
        max_iterations=settings.max_iterations,
        max_human_review_rounds=settings.max_human_review_rounds,
    )

    detected_lang = language_config.detect_language(repo_context.list_files(repo_path))
    print(f"Detected language: {detected_lang.display_name}")

    print(f"Analyzing requirement against {repo_path} ...")
    state.plan = requirement_analyzer.analyze(requirement, build_analysis_context(repo_path, requirement))

    # Mandatory human gate -- no path through this function reaches
    # code_generator.generate() without an explicit approval. A rejection that
    # exhausts max_plan_review_rounds stops the run here, before anything is
    # written, rather than falling through to code generation.
    if not get_plan_approval(state, repo_path, requirement):
        print(
            f"\n=== FAILED -- plan never approved after {state.plan_review_rounds} round(s) ==="
        )
        print(f"Last plan: {state.plan.summary}")
        print(f"Last rejection feedback: {state.plan_feedback}")
        write_output(state)
        _log_run(state, settings, github_tools.is_configured(), "plan_rejected")
        return

    print(f"\nPlan approved: {state.plan.summary}")
    print(f"Files to touch: {state.plan.files_to_touch}")

    # NOTE: github_repo is a GitHub "owner/repo" slug, unrelated to state.repo_url
    # (which is the local sandbox path) -- see tools/github_tools.py's module docstring.
    github_repo = settings.repo
    github_enabled = github_tools.is_configured()
    if not github_enabled:
        print("GITHUB_TOKEN/GITHUB_REPO not configured -- skipping GitHub PR/review phase.")

    try:
        outcome = regenerate_and_push(
            state, repo_path, github_repo, github_enabled, ticket_id, feedback=None, run_automated_review=True
        )
    except github_tools.GitHubConfigError as exc:
        print(f"GitHub step failed: {exc}")
        outcome = "github_config_error"

    write_output(state)
    _log_run(state, settings, github_enabled, outcome)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Registers this command's flags on `parser` -- shared by `python -m
    adapters.cli_adapter.run` (below) and `agentdev run` (adapters/cli_adapter/main.py),
    so the two stay identical by construction rather than by two hand-kept flag lists."""
    default_base_branch = config.resolve_settings().base_branch

    parser.add_argument(
        "--requirement",
        default=None,
        help="Natural-language description of the change to make (required)",
    )
    parser.add_argument(
        "--repo-path",
        default=None,
        help="Local path to the repo to read/edit (default: this project's root)",
    )
    parser.add_argument("--base-branch", default=default_base_branch)
    parser.add_argument("--ticket-id", default=None, help="e.g. AD-101 -- used for branch/commit/PR naming")
    parser.add_argument(
        "--skip-tests",
        dest="skip_tests",
        action="store_true",
        default=True,
        help="Skip the Docker/pytest test-and-lint step (default: on)",
    )
    parser.add_argument(
        "--run-tests",
        dest="skip_tests",
        action="store_false",
        help="Re-enable the Docker/pytest test-and-lint retry loop",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentdev run", description="Run the coding-agent loop against a repo.")
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        run(args.requirement, args.repo_path, args.base_branch, args.ticket_id, args.skip_tests)
    except RequirementError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
