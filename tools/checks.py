"""Live validation checks shared by `agentdev init` (adapters/cli_adapter/init.py)
and `agentdev doctor` (adapters/cli_adapter/doctor.py) -- both need to answer the
same questions (does gh auth work, can this token push, does the Anthropic key
work), just with different framing (interactive retry vs. pass/fail report)."""

import subprocess

import anthropic
from github import Auth, Github, GithubException

_GH_TIMEOUT = 10
_GITHUB_API_TIMEOUT = 15


def gh_authenticated() -> bool:
    try:
        result = subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=_GH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def validate_repo_and_branch(token: str | None, repo_slug: str | None, base_branch: str) -> tuple[bool, str]:
    if not repo_slug:
        return False, "no repo configured"
    if not token:
        return False, "no GitHub token available to validate against"
    try:
        client = Github(auth=Auth.Token(token), timeout=_GITHUB_API_TIMEOUT)
        gh_repo = client.get_repo(repo_slug)
        gh_repo.get_branch(base_branch)
    except GithubException as exc:
        return False, f"could not reach {repo_slug}@{base_branch}: {exc.data.get('message', exc)}"
    return True, f"{repo_slug}@{base_branch} is reachable"


def validate_push_rights(token: str | None, repo_slug: str | None) -> tuple[bool, str]:
    if not repo_slug:
        return False, "no repo configured"
    if not token:
        return False, "no GitHub token available to validate against"
    try:
        client = Github(auth=Auth.Token(token), timeout=_GITHUB_API_TIMEOUT)
        gh_repo = client.get_repo(repo_slug)
        permissions = gh_repo.permissions
    except GithubException as exc:
        return False, f"could not check permissions on {repo_slug}: {exc.data.get('message', exc)}"
    if permissions is None or not permissions.push:
        return False, f"token does not have push access to {repo_slug}"
    return True, f"token has push access to {repo_slug}"


def validate_anthropic_key() -> tuple[bool, str]:
    try:
        anthropic.Anthropic().models.list(timeout=_GITHUB_API_TIMEOUT)
    except TypeError:
        # The SDK raises a bare TypeError at request-build time (before any
        # HTTP call) when no API key is configured at all -- ANTHROPIC_API_KEY
        # unset, not just invalid.
        return False, "ANTHROPIC_API_KEY is not set"
    except anthropic.AuthenticationError:
        return False, "ANTHROPIC_API_KEY is invalid"
    except anthropic.APIError as exc:
        return False, f"Anthropic API call failed: {exc}"
    return True, "ANTHROPIC_API_KEY works"
