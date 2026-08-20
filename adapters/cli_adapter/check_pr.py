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

import os

from dotenv import load_dotenv

load_dotenv()

from adapters.cli_adapter.run import (
    build_fixup_commit_message,
    build_human_review_feedback,
    regenerate_and_push,
)
from core import routing
from tools import github_tools, state_store


def check_pr(ticket_id: str) -> None:
    state = state_store.load(ticket_id)
    if state is None or state.pr_number is None:
        print(f"No PR on record for ticket {ticket_id} -- run the main pipeline first.")
        return

    github_repo = os.environ.get("GITHUB_REPO")

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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Check a PR's human review status and push a revision if changes were requested."
    )
    parser.add_argument("--ticket-id", required=True, help="e.g. AD-101 -- must match a prior run's ticket")
    args = parser.parse_args()

    check_pr(args.ticket_id)
