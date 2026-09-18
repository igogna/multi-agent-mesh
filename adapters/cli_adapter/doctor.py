"""`agentdev doctor` -- non-interactive preflight checks, each pass/fail with
a one-line fix. `run` calls run_fast_checks() (everything except the Docker
daemon check) before doing any work, since skip_tests defaults to True and
Docker is only actually needed once tests are re-enabled.
"""

import argparse
import subprocess
from dataclasses import dataclass

import docker
from docker.errors import DockerException

from tools import checks, config

_GIT_CONFIG_TIMEOUT = 5


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    fix: str | None = None


def _git_config_value(key: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "config", key], capture_output=True, text=True, timeout=_GIT_CONFIG_TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def check_git_identity() -> CheckResult:
    name = _git_config_value("user.name")
    email = _git_config_value("user.email")
    if name and email:
        return CheckResult("git identity", True, f"user.name={name!r}, user.email={email!r}")
    return CheckResult(
        "git identity",
        False,
        "git user.name/user.email not set -- commits made on your behalf would fail",
        fix='git config --global user.name "Your Name" && git config --global user.email you@example.com',
    )


def check_agentdev_toml(settings: config.Settings) -> CheckResult:
    if settings.project_root is None:
        return CheckResult(
            "project config", False, "not inside a git repository", fix="cd into your project first"
        )
    path = settings.project_root / config.PROJECT_CONFIG_FILENAME
    if not path.is_file():
        return CheckResult(
            "project config", False, f"{path} not found", fix="Run: agentdev init"
        )
    try:
        config.load_project_config(settings.project_root)
    except Exception as exc:  # tomllib raises TOMLDecodeError; keep this check broad on purpose
        return CheckResult(
            "project config", False, f"{path} is not valid TOML: {exc}", fix=f"Fix or regenerate {path}"
        )
    return CheckResult("project config", True, f"{path} found and parses")


def check_anthropic_key() -> CheckResult:
    ok, message = checks.validate_anthropic_key()
    return CheckResult(
        "ANTHROPIC_API_KEY",
        ok,
        message,
        fix=None if ok else "Set ANTHROPIC_API_KEY in your environment (get one at console.anthropic.com)",
    )


def check_github_auth(settings: config.Settings) -> CheckResult:
    if settings.github_token is None:
        return CheckResult(
            "GitHub auth",
            False,
            "no usable GitHub token (gh CLI, GITHUB_TOKEN, or user config)",
            fix="Run `gh auth login`, or set GITHUB_TOKEN, or run `agentdev init`",
        )
    ok, message = checks.validate_push_rights(settings.github_token, settings.repo)
    return CheckResult(
        "GitHub auth",
        ok,
        message,
        fix=None if ok else "Re-run `gh auth login`, or check the token has push access to the repo",
    )


def check_base_branch(settings: config.Settings) -> CheckResult:
    if settings.repo is None:
        return CheckResult(
            "base branch", False, "no repo configured", fix="Run: agentdev init"
        )
    ok, message = checks.validate_repo_and_branch(settings.github_token, settings.repo, settings.base_branch)
    return CheckResult(
        "base branch",
        ok,
        message,
        fix=None if ok else f"Check base_branch in .agentdev.toml matches a real branch on {settings.repo}",
    )


def check_docker() -> CheckResult:
    try:
        client = docker.from_env(timeout=10)
        client.ping()
    except DockerException as exc:
        return CheckResult(
            "Docker daemon",
            False,
            f"not reachable: {exc}",
            fix="Start Docker Desktop (only needed for `agentdev run --run-tests`)",
        )
    return CheckResult("Docker daemon", True, "reachable")


def run_fast_checks(settings: config.Settings) -> list[CheckResult]:
    """Everything except the Docker check -- what `agentdev run` runs before
    doing any work, since Docker is only needed once tests are re-enabled."""
    return [
        check_git_identity(),
        check_agentdev_toml(settings),
        check_anthropic_key(),
        check_github_auth(settings),
        check_base_branch(settings),
    ]


def run_all_checks(settings: config.Settings) -> list[CheckResult]:
    return run_fast_checks(settings) + [check_docker()]


def print_results(results: list[CheckResult]) -> bool:
    all_passed = True
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}")
        if not result.passed:
            all_passed = False
            if result.fix:
                print(f"       fix: {result.fix}")
    return all_passed


def main(args: argparse.Namespace) -> int:
    settings = config.resolve_settings()
    results = run_all_checks(settings)
    all_passed = print_results(results)
    return 0 if all_passed else 1
