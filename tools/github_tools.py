"""GitHub integration: local git operations (via GitPython) plus the GitHub API
(via PyGithub). NOTE: `repo_url` here means a GitHub "owner/repo" slug -- a
different thing from core.models.RunState.repo_url, which is a local
filesystem path used for sandbox testing. Do not conflate the two.
"""

import base64
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import git
from github import Auth, Github

from core.models import FileChange, HumanReviewIssue, HumanReviewResult, TestFile
from tools import checks, config as agentdev_config
from tools.workspace import normalize_permissions, write_changes

_GH_SETUP_GIT_TIMEOUT = 10
_GITHUB_API_TIMEOUT = 15

# One local clone per repo slug, reused for the life of the process (including
# every commit within a single run, e.g. review-triggered retries onto the
# same branch).
_CLONE_CACHE: dict[str, Path] = {}

# Authenticated user's login, cached per token, so get_pr_status can filter the
# bot's own reviews/comments (post_review_comment posts plain issue-comments
# indistinguishable by shape from a human's) out of human-review reads.
_LOGIN_CACHE: dict[str, str] = {}

_REVIEW_STATE_TO_VERDICT: dict[str, Literal["approved", "changes_requested", "commented"]] = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes_requested",
    "COMMENTED": "commented",
}


class GitHubConfigError(RuntimeError):
    pass


def is_configured() -> bool:
    settings = agentdev_config.resolve_settings()
    return settings.repo is not None and settings.github_token is not None


def _require_token() -> str:
    settings = agentdev_config.resolve_settings()
    if settings.repo is None or settings.github_token is None:
        raise GitHubConfigError(
            "GITHUB_TOKEN/GITHUB_REPO are unset or still placeholders -- cannot reach GitHub."
        )
    return settings.github_token


def _get_github_client(token: str) -> Github:
    return Github(auth=Auth.Token(token), timeout=_GITHUB_API_TIMEOUT)


_gh_setup_git_done = False  # process-local: run `gh auth setup-git` at most once


def _configure_gh_credential_helper() -> None:
    global _gh_setup_git_done
    if _gh_setup_git_done:
        return
    try:
        subprocess.run(
            ["gh", "auth", "setup-git"], capture_output=True, timeout=_GH_SETUP_GIT_TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    _gh_setup_git_done = True


def _github_auth_header(token: str) -> str:
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"AUTHORIZATION: Basic {basic}"


def _local_clone(repo_slug: str, token: str) -> git.Repo:
    if repo_slug in _CLONE_CACHE:
        return git.Repo(_CLONE_CACHE[repo_slug])
    workdir = Path(tempfile.mkdtemp(prefix="github_clone_"))
    remote = f"https://github.com/{repo_slug}.git"

    if checks.gh_authenticated():
        # gh's own git credential helper handles auth transparently -- the
        # token never touches the URL or this clone's local git config.
        _configure_gh_credential_helper()
        repo = git.Repo.clone_from(remote, workdir)
    else:
        # No gh CLI: scope the credential to github.com specifically, set via
        # -c on the clone command itself. Never in the remote URL -- that's
        # what writes it into .git/config's [remote "origin"] and into git's
        # own error output (which prints the failing URL verbatim).
        # GitPython rejoins multi_options with spaces and re-splits them with
        # shlex, so the header value (which itself contains a space) must be
        # shell-quoted or that round-trip breaks it into extra tokens.
        auth_option = f"http.https://github.com/.extraheader={_github_auth_header(token)}"
        repo = git.Repo.clone_from(
            remote, workdir, multi_options=["-c", shlex.quote(auth_option)], allow_unsafe_options=True
        )

    _CLONE_CACHE[repo_slug] = workdir
    return repo


def create_branch(repo_url: str, base_branch: str, branch_name: str) -> str:
    token = _require_token()
    repo = _local_clone(repo_url, token)
    repo.remotes.origin.fetch()
    repo.git.checkout(base_branch)
    repo.remotes.origin.pull()
    if branch_name in [head.name for head in repo.heads]:
        repo.git.branch("-D", branch_name)
    repo.git.checkout("-b", branch_name)
    return branch_name


def commit_and_push(
    repo_url: str,
    branch_name: str,
    file_changes: list[FileChange],
    test_files: list[TestFile],
    commit_message: str,
) -> None:
    token = _require_token()
    repo = _local_clone(repo_url, token)
    workdir = Path(repo.working_tree_dir)
    write_changes(workdir, file_changes, test_files)
    normalize_permissions(workdir)

    repo.git.add(all=True)
    if repo.is_dirty(untracked_files=True):
        repo.git.commit("-m", commit_message)

    # Force-push is deliberate: this only ever targets agent-owned branches
    # created by create_branch() above, never base_branch or a human's branch.
    repo.git.push("origin", branch_name, set_upstream=True, force=True)


def open_pr(
    repo_url: str, branch_name: str, base_branch: str, title: str, body: str
) -> tuple[int, str]:
    token = _require_token()
    gh_repo = _get_github_client(token).get_repo(repo_url)

    owner = gh_repo.owner.login
    existing = list(gh_repo.get_pulls(state="open", head=f"{owner}:{branch_name}"))
    if existing:
        pr = existing[0]
        return pr.number, pr.html_url

    pr = gh_repo.create_pull(title=title, body=body, head=branch_name, base=base_branch)
    return pr.number, pr.html_url


def post_review_comment(repo_url: str, pr_number: int, body: str) -> None:
    token = _require_token()
    gh_repo = _get_github_client(token).get_repo(repo_url)
    gh_repo.get_pull(pr_number).create_issue_comment(body)


def get_current_login() -> str:
    """The authenticated dev's GitHub login -- used to namespace branch names
    (see adapters/cli_adapter/run.py's make_branch_name) so two devs working
    the same ticket don't force-push over each other's branch."""
    token = _require_token()
    return _authenticated_login(_get_github_client(token), token)


def _authenticated_login(client: Github, token: str) -> str:
    if token not in _LOGIN_CACHE:
        _LOGIN_CACHE[token] = client.get_user().login
    return _LOGIN_CACHE[token]


def get_pr_status(repo_url: str, pr_number: int) -> HumanReviewResult:
    """Cheap, LLM-free read of a PR's human-review state: one PR fetch plus a
    couple of paginated list reads, no git clone, no LLM call. Distinguishes
    merged / closed-unmerged / open; for open PRs, collapses reviews to the
    latest *submitted* verdict per reviewer (ignoring the bot's own reviews/
    comments and non-terminal states like PENDING/DISMISSED), and attaches
    inline comments tied to the latest changes-requested review.
    """
    token = _require_token()
    client = _get_github_client(token)
    gh_repo = client.get_repo(repo_url)
    pr = gh_repo.get_pull(pr_number)
    bot_login = _authenticated_login(client, token)

    if pr.merged:
        return HumanReviewResult(pr_state="merged", verdict="approved")
    if pr.state == "closed":
        return HumanReviewResult(pr_state="closed_unmerged", verdict="no_review_yet")

    latest_by_user = {}
    for review in pr.get_reviews():
        if review.user.login == bot_login or review.state not in _REVIEW_STATE_TO_VERDICT:
            continue
        latest_by_user[review.user.login] = review  # later reviews overwrite earlier ones

    reviewers = {
        login: _REVIEW_STATE_TO_VERDICT[review.state] for login, review in latest_by_user.items()
    }
    if any(v == "changes_requested" for v in reviewers.values()):
        verdict: Literal["approved", "changes_requested", "commented_only", "no_review_yet"] = (
            "changes_requested"
        )
    elif any(v == "approved" for v in reviewers.values()):
        verdict = "approved"
    elif any(v == "commented" for v in reviewers.values()):
        verdict = "commented_only"
    else:
        verdict = "no_review_yet"

    latest_review_id = None
    feedback_items: list[HumanReviewIssue] = []
    if verdict == "changes_requested":
        change_requests = [r for r in latest_by_user.values() if r.state == "CHANGES_REQUESTED"]
        latest_review = max(change_requests, key=lambda r: r.id)
        latest_review_id = latest_review.id
        if latest_review.body:
            feedback_items.append(
                HumanReviewIssue(
                    author=latest_review.user.login, body=latest_review.body, review_id=latest_review.id
                )
            )
        for comment in pr.get_review_comments():
            if comment.pull_request_review_id == latest_review.id:
                feedback_items.append(
                    HumanReviewIssue(
                        author=comment.user.login,
                        body=comment.body,
                        comment_id=comment.id,
                        path=comment.path,
                        line=comment.line,
                    )
                )

    new_comment_ids = [c.id for c in pr.get_issue_comments() if c.user.login != bot_login]

    return HumanReviewResult(
        pr_state="open",
        verdict=verdict,
        latest_review_id=latest_review_id,
        reviewers=reviewers,
        feedback_items=feedback_items,
        new_comment_ids=new_comment_ids,
    )


def get_pr_diff(repo_url: str, pr_number: int) -> str:
    # Not used by the automated pipeline today -- the review step builds its
    # diff locally from FileChange.diff, which exists before any PR does.
    # Kept as a manual/future-use utility.
    token = _require_token()
    gh_repo = _get_github_client(token).get_repo(repo_url)
    pr = gh_repo.get_pull(pr_number)
    return "\n".join(f.patch or "" for f in pr.get_files())
