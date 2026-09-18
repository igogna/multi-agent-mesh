"""Resolves the three config layers (CLI flag > env var > project config >
user config > default) into one typed Settings object.

- Project config: `.agentdev.toml` at the project's git root (committed --
  repo, base_branch, ticket_prefix, model, max_iterations,
  max_human_review_rounds).
- User config: `~/.config/agentdev/config.toml` (never committed --
  github_token fallback only, see resolve_github_token).
- Environment: GITHUB_REPO / GITHUB_BASE_BRANCH / GITHUB_TOKEN, kept under
  their existing names. ANTHROPIC_API_KEY is deliberately never read here --
  the Anthropic SDK picks it up on its own in core/_llm.py.

github_token has its own precedence (see resolve_github_token): a dev's
existing `gh` CLI session outranks everything else, since it's live and
already scoped/rotatable, unlike a stored token.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_BASE_BRANCH = "main"
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_MAX_HUMAN_REVIEW_ROUNDS = 3

_PLACEHOLDER_VALUES = {None, "", "<<GITHUB_TOKEN>>", "<<GITHUB_REPO>>", "<<GITHUB_BASE_BRANCH>>"}

PROJECT_CONFIG_FILENAME = ".agentdev.toml"
USER_CONFIG_PATH = Path.home() / ".config" / "agentdev" / "config.toml"

_GH_AUTH_TOKEN_TIMEOUT = 5


@dataclass
class Settings:
    repo: str | None
    base_branch: str
    ticket_prefix: str | None
    model: str
    max_iterations: int
    max_human_review_rounds: int
    github_token: str | None
    project_root: Path | None


def find_project_root(start: Path | None = None) -> Path | None:
    """Walks up from `start` (default cwd) to the nearest git root."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _clean_env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value not in _PLACEHOLDER_VALUES else None


def load_project_config(project_root: Path | None) -> dict:
    if project_root is None:
        return {}
    path = project_root / PROJECT_CONFIG_FILENAME
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_user_config() -> dict:
    if not USER_CONFIG_PATH.is_file():
        return {}
    with USER_CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def _gh_auth_token() -> str | None:
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=_GH_AUTH_TOKEN_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def resolve_github_token(user_config: dict | None = None) -> str | None:
    """1. `gh auth token` (a dev's existing gh CLI session), 2. GITHUB_TOKEN
    env var, 3. the user config file. Never consults project config -- a
    token must never be committed to a project's repo."""
    gh_token = _gh_auth_token()
    if gh_token:
        return gh_token

    env_token = _clean_env("GITHUB_TOKEN")
    if env_token:
        return env_token

    if user_config is None:
        user_config = load_user_config()
    token = user_config.get("github_token")
    return token if token not in _PLACEHOLDER_VALUES else None


def resolve_settings(
    *,
    repo: str | None = None,
    base_branch: str | None = None,
    ticket_prefix: str | None = None,
    model: str | None = None,
    max_iterations: int | None = None,
    max_human_review_rounds: int | None = None,
    start_dir: Path | None = None,
) -> Settings:
    """Resolves one Settings object. Keyword args represent CLI-flag
    overrides -- pass the value only when the flag was actually given, so a
    None here correctly falls through to the next layer instead of masking
    it."""
    project_root = find_project_root(start_dir)
    try:
        project_config = load_project_config(project_root)
    except Exception:
        # A malformed .agentdev.toml shouldn't crash every command -- fall
        # back to the other layers here; `agentdev doctor` (which calls
        # load_project_config directly) is what reports the parse error.
        project_config = {}
    try:
        user_config = load_user_config()
    except Exception:
        user_config = {}

    def pick(cli_value, env_name: str | None, key: str, default):
        if cli_value is not None:
            return cli_value
        if env_name is not None:
            env_value = _clean_env(env_name)
            if env_value is not None:
                return env_value
        if key in project_config and project_config[key] not in _PLACEHOLDER_VALUES:
            return project_config[key]
        if key in user_config and user_config[key] not in _PLACEHOLDER_VALUES:
            return user_config[key]
        return default

    return Settings(
        repo=pick(repo, "GITHUB_REPO", "repo", None),
        base_branch=pick(base_branch, "GITHUB_BASE_BRANCH", "base_branch", DEFAULT_BASE_BRANCH),
        ticket_prefix=pick(ticket_prefix, None, "ticket_prefix", None),
        model=pick(model, None, "model", DEFAULT_MODEL),
        max_iterations=pick(max_iterations, None, "max_iterations", DEFAULT_MAX_ITERATIONS),
        max_human_review_rounds=pick(
            max_human_review_rounds, None, "max_human_review_rounds", DEFAULT_MAX_HUMAN_REVIEW_ROUNDS
        ),
        github_token=resolve_github_token(user_config),
        project_root=project_root,
    )
