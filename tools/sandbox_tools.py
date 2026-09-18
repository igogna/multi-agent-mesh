import importlib.resources
import json
import shutil
import tempfile
from pathlib import Path

import docker
from docker.errors import DockerException, ImageNotFound

from core.models import FileChange, LintResults, TestFile, TestResults
from tools import language_config, repo_context
from tools.workspace import normalize_permissions, write_changes


def _resolve_sandbox_root() -> Path:
    """Prefers the package data shipped in the wheel (see pyproject.toml's
    force-include + hatch_build.py) so this works after `pip install`/`uv tool
    install`, with no source checkout on disk; falls back to the source-
    checkout layout so `python -m adapters.cli_adapter.run` keeps working
    unchanged in dev."""
    try:
        packaged = Path(str(importlib.resources.files("adapters.cli_adapter") / "_data" / "sandbox"))
        if packaged.is_dir():
            return packaged
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    return Path(__file__).resolve().parent.parent / "sandbox"


SANDBOX_ROOT = _resolve_sandbox_root()

# Generous but bounded: a real test/lint run inside the sandbox should never
# legitimately need this long, but a runaway/hung container must not block a
# run forever.
CONTAINER_WAIT_TIMEOUT_SECONDS = 600


def _get_docker_client() -> docker.DockerClient:
    try:
        client = docker.from_env()
        client.ping()
    except DockerException as exc:
        raise RuntimeError(
            "Docker Desktop does not appear to be running. Start Docker Desktop and retry."
        ) from exc
    return client


def _image_tag(lang: language_config.LanguageConfig) -> str:
    return f"coding-agent-sandbox-{lang.id}:latest"


def _ensure_image(client: docker.DockerClient, lang: language_config.LanguageConfig) -> str:
    tag = _image_tag(lang)
    try:
        client.images.get(tag)
    except ImageNotFound:
        client.images.build(path=str(SANDBOX_ROOT / lang.dockerfile_dir), tag=tag)
    return tag


def _detect_language(repo_path: str) -> language_config.LanguageConfig:
    return language_config.detect_language(repo_context.list_files(repo_path))


def _materialize_workspace(
    repo_path: str, file_changes: list[FileChange], test_files: list[TestFile]
) -> Path:
    workdir = Path(tempfile.mkdtemp(prefix="sandbox_"))
    shutil.copytree(repo_path, workdir, dirs_exist_ok=True)
    write_changes(workdir, file_changes, test_files)
    normalize_permissions(workdir)
    return workdir


def _run_in_container(
    client: docker.DockerClient, image_tag: str, workdir: Path, command: str | list[str]
) -> tuple[int, str]:
    container = client.containers.run(
        image_tag,
        command=command,
        volumes={str(workdir): {"bind": "/workspace", "mode": "rw"}},
        working_dir="/workspace",
        detach=True,
    )
    try:
        try:
            exit_code = container.wait(timeout=CONTAINER_WAIT_TIMEOUT_SECONDS)["StatusCode"]
        except Exception as exc:
            container.stop(timeout=1)
            raise RuntimeError(
                f"Sandbox container did not finish within {CONTAINER_WAIT_TIMEOUT_SECONDS}s -- stopped it."
            ) from exc
        output = container.logs().decode("utf-8", errors="replace")
    finally:
        container.remove(force=True)
    return exit_code, output


def _extract_coverage_pct(lang: language_config.LanguageConfig, workdir: Path) -> float | None:
    # Only pytest-cov's coverage.json is understood today -- other languages'
    # test commands don't produce a coverage artifact yet, so this stays None
    # for them rather than guessing at a format.
    if lang.id != "python":
        return None
    coverage_file = workdir / "coverage.json"
    if not coverage_file.exists():
        return None
    try:
        coverage_data = json.loads(coverage_file.read_text(encoding="utf-8"))
        return coverage_data.get("totals", {}).get("percent_covered")
    except (json.JSONDecodeError, OSError):
        return None


def run_tests(
    repo_path: str, file_changes: list[FileChange], test_files: list[TestFile]
) -> TestResults:
    lang = _detect_language(repo_path)
    client = _get_docker_client()
    image_tag = _ensure_image(client, lang)
    workdir = _materialize_workspace(repo_path, file_changes, test_files)
    try:
        exit_code, output = _run_in_container(client, image_tag, workdir, lang.test_command)
        coverage_pct = _extract_coverage_pct(lang, workdir)
        return TestResults(passed=exit_code == 0, output=output, coverage_pct=coverage_pct)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_lint(repo_path: str, file_changes: list[FileChange]) -> LintResults:
    lang = _detect_language(repo_path)
    if lang.lint_command is None:
        # No linter wired up for this language yet -- treat as a no-op pass
        # rather than starting a container for nothing.
        return LintResults(passed=True, issues=[])

    client = _get_docker_client()
    image_tag = _ensure_image(client, lang)
    workdir = _materialize_workspace(repo_path, file_changes, [])
    try:
        exit_code, output = _run_in_container(client, image_tag, workdir, lang.lint_command)
        parse = lang.parse_lint or language_config.default_parse_lint
        issues = parse(output, exit_code)
        return LintResults(passed=exit_code == 0, issues=issues)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
