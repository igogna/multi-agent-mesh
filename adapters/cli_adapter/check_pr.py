"""Second entry point into the pipeline, separate from run.py's initial
requirement -> PR flow. check_pr resumes a persisted RunState for one ticket,
makes a single cheap GitHub read (no LLM call) to see what a human did on the
PR, and only invokes the expensive regen path when there's genuinely new,
actionable human feedback -- never re-deriving the plan or opening a new PR,
since a human review is a continuation of the same requirement, not a new one.

Runs once per invocation and exits -- no poll loop. Re-check a ticket by
running this again (by hand today; a cron/webhook can call check_pr()
directly later without any change to this function).
"""

import argparse

from dotenv import load_dotenv

load_dotenv()

from adapters.cli_adapter.run import (
    build_fixup_commit_message,
    build_human_review_feedback,
    regenerate_and_push,
)
from core import _llm, routing
from tools import config, github_tools, state_store


def check_pr(ticket_id: str) -> None:
    state = state_store.load(ticket_id)
    if state is None or state.pr_number is None:
        print(f"No PR on record for ticket {ticket_id} -- run the main pipeline first.")
        return

    settings = config.resolve_settings()
    _llm.set_model(settings.model)
    github_repo = settings.repo

    print(f"Checking PR #{state.pr_number} for ticket {ticket_id} ...")
    state.human_review = github_tools.get_pr_status(github_repo, state.pr_number)
    decision = routing.decide_after_human_review(state)
    print(f"Human review decision: {decision}")

    if decision == "merged":
        state.human_review_state = "merged"
        print(f"PR #{state.pr_number} was merged by a human -- nothing further to do.")
    elif decision == "closed_unmerged":
        state.human_review_state = "closed_unmerged"
        print(f"PR #{state.pr_number} was closed without merging -- pipeline stops here.")
    elif decision == "await_human":
        print("No new actionable human feedback yet.")
    elif decision == "escalate":
        print(
            f"Human review round budget exhausted "
            f"({state.human_review_rounds}/{state.max_human_review_rounds}) -- needs manual triage."
        )
        print(f"PR left open for human triage: {state.pr_url}")
    else:  # "regenerate"
        feedback = build_human_review_feedback(state.human_review)
        state.last_processed_review_id = state.human_review.latest_review_id
        state.human_review_rounds += 1
        state.human_has_reviewed = True

        github_tools.post_review_comment(
            github_repo,
            state.pr_number,
            f"Pushed a revision addressing requested changes (round {state.human_review_rounds}).",
        )

        try:
            regenerate_and_push(
                state,
                state.repo_url,
                github_repo,
                github_enabled=True,
                ticket_id=ticket_id,
                feedback=feedback,
                run_automated_review=False,
                commit_message=build_fixup_commit_message(ticket_id, state.human_review_rounds),
            )
        except github_tools.GitHubConfigError as exc:
            print(f"GitHub step failed: {exc}")

    state_store.save(state)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Registers this command's flags on `parser` -- shared by `python -m
    adapters.cli_adapter.check_pr` (below) and `agentdev check`
    (adapters/cli_adapter/main.py)."""
    parser.add_argument("--ticket-id", required=True, help="e.g. AD-101 -- must match a prior run's ticket")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentdev check",
        description="Check a PR's human review status and push a revision if changes were requested.",
    )
    add_arguments(parser)
    args = parser.parse_args(argv)
    check_pr(args.ticket_id)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
