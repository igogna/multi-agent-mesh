"""`agentdev init` -- interactive per-project setup. Detects the git remote
and its default branch, prefers an existing `gh` CLI session over asking for
a token, validates every answer live, and writes .agentdev.toml (project,
committed) plus ~/.config/agentdev/config.toml (user, token fallback only).
"""

import argparse
import getpass
from pathlib import Path

import git

from tools import checks, config


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{text}{suffix}: ").strip()
    return value or default


def _prompt_int(text: str, default: int) -> int:
    while True:
        raw = _prompt(text, str(default))
        try:
            return int(raw)
        except ValueError:
            print(f"  Not a number: {raw!r}")


def _prompt_until_valid(text: str, default: str, validate) -> str:
    """Prompts for a value, runs `validate(value) -> (ok, message)` against
    it, and either accepts or lets the dev retry -- this is the "validate
    each answer live" requirement, not just a one-shot format check."""
    while True:
        value = _prompt(text, default)
        ok, message = validate(value)
        print(f"  {'OK' if ok else 'FAILED'}: {message}")
        if ok:
            return value
        if _prompt("  Try a different value? [Y/n]", "Y").lower().startswith("n"):
            return value


def _detect_remote_slug(project_root: Path) -> str | None:
    try:
        repo = git.Repo(project_root)
        url = repo.remotes.origin.url
    except (git.InvalidGitRepositoryError, AttributeError, ValueError):
        return None
    import re

    match = re.search(r"github\.com[:/](?P<slug>[^/]+/[^/]+?)(?:\.git)?/?$", url)
    return match.group("slug") if match else None


def _detect_default_branch(project_root: Path) -> str | None:
    try:
        repo = git.Repo(project_root)
        output = repo.git.ls_remote("--symref", "origin", "HEAD")
    except (git.InvalidGitRepositoryError, git.GitCommandError, AttributeError):
        return None
    for line in output.splitlines():
        # e.g. "ref: refs/heads/main\tHEAD"
        if line.startswith("ref:") and "\t" in line:
            ref = line.split()[1]
            if ref.startswith("refs/heads/"):
                return ref[len("refs/heads/") :]
    return None


def _dump_toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _dump_toml(data: dict) -> str:
    return "\n".join(f"{key} = {_dump_toml_value(value)}" for key, value in data.items()) + "\n"


def _write_user_token(token: str) -> Path:
    user_config = config.load_user_config()
    user_config["github_token"] = token
    config.USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.USER_CONFIG_PATH.write_text(_dump_toml(user_config), encoding="utf-8")
    try:
        config.USER_CONFIG_PATH.chmod(0o600)
    except NotImplementedError:
        pass  # chmod is a no-op on some filesystems (e.g. certain Windows setups)
    return config.USER_CONFIG_PATH


def main(args: argparse.Namespace) -> int:
    project_root = config.find_project_root()
    if project_root is None:
        print("agentdev init must be run inside a git repository (no .git found above the current directory).")
        return 1

    project_config_path = project_root / config.PROJECT_CONFIG_FILENAME
    if project_config_path.exists() and not getattr(args, "force", False):
        print(f"{project_config_path} already exists. Re-run with --force to overwrite it.")
        return 1

    print(f"Setting up agentdev for {project_root}\n")

    detected_repo = _detect_remote_slug(project_root)
    repo = _prompt("GitHub repo (owner/repo, blank to skip GitHub integration)", detected_repo or "")

    github_token: str | None = None
    base_branch = "main"
    project_data: dict[str, str | int] = {}

    if repo:
        detected_branch = _detect_default_branch(project_root) or "main"
        base_branch = _prompt("Base branch", detected_branch)

        gh_ready = checks.gh_authenticated()
        if gh_ready:
            print("  Using the existing `gh` CLI session -- no token needed.")
            github_token = config.resolve_github_token()
        else:
            github_token = getpass.getpass("GitHub token (hidden, needs repo push access): ").strip() or None

        repo = _prompt_until_valid(
            "GitHub repo (owner/repo)", repo, lambda v: checks.validate_repo_and_branch(github_token, v, base_branch)
        )
        if not gh_ready and github_token:
            _prompt_until_valid(
                "(press Enter to re-check push rights)",
                "",
                lambda _: checks.validate_push_rights(github_token, repo),
            )

        project_data["repo"] = repo
        project_data["base_branch"] = base_branch

    ticket_prefix = _prompt("Ticket prefix (e.g. AD, blank for none)", "")
    if ticket_prefix:
        project_data["ticket_prefix"] = ticket_prefix

    model = _prompt("Model", config.DEFAULT_MODEL)
    project_data["model"] = model

    project_data["max_iterations"] = _prompt_int("Max iterations", config.DEFAULT_MAX_ITERATIONS)
    project_data["max_human_review_rounds"] = _prompt_int(
        "Max human review rounds", config.DEFAULT_MAX_HUMAN_REVIEW_ROUNDS
    )

    print()
    ok, message = checks.validate_anthropic_key()
    print(f"  {'OK' if ok else 'WARNING'}: {message}")
    if not ok:
        print("  (agentdev run/check will fail until ANTHROPIC_API_KEY is set in your environment.)")

    project_config_path.write_text(_dump_toml(project_data), encoding="utf-8")
    print(f"\nWrote {project_config_path}")

    if repo and not gh_ready and github_token:
        user_path = _write_user_token(github_token)
        print(f"Wrote {user_path} (chmod 600)")

    print("\nDone. Run `agentdev doctor` to double-check everything before your first `agentdev run`.")
    return 0
