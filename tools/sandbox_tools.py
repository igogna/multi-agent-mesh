import json
import shutil
import tempfile
from pathlib import Path

import docker
from docker.errors import DockerException, ImageNotFound

from core.models import FileChange, LintResults, TestFile, TestResults
from tools.workspace import normalize_permissions, write_changes

IMAGE_TAG = "coding-agent-sandbox:latest"
DOCKERFILE_DIR = Path(__file__).resolve().parent.parent / "sandbox"

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


def _ensure_image(client: docker.DockerClient) -> None:
    try:
        client.images.get(IMAGE_TAG)
    except ImageNotFound:
        client.images.build(path=str(DOCKERFILE_DIR), tag=IMAGE_TAG)


def _materialize_workspace(
    repo_path: str, file_changes: list[FileChange], test_files: list[TestFile]
) -> Path:
    workdir = Path(tempfile.mkdtemp(prefix="sandbox_"))
    shutil.copytree(repo_path, workdir, dirs_exist_ok=True)
    write_changes(workdir, file_changes, test_files)
    normalize_permissions(workdir)
    return workdir


def _run_in_container(
    client: docker.DockerClient, workdir: Path, command: str | list[str]
) -> tuple[int, str]:
    container = client.containers.run(
        IMAGE_TAG,
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


def run_tests(
    repo_path: str, file_changes: list[FileChange], test_files: list[TestFile]
) -> TestResults:
    client = _get_docker_client()
    _ensure_image(client)
    workdir = _materialize_workspace(repo_path, file_changes, test_files)
    try:
        exit_code, output = _run_in_container(
            client, workdir, "pytest --cov=. --cov-report=json:coverage.json -q"
        )

        coverage_pct = None
        coverage_file = workdir / "coverage.json"
        if coverage_file.exists():
            try:
                coverage_data = json.loads(coverage_file.read_text(encoding="utf-8"))
                coverage_pct = coverage_data.get("totals", {}).get("percent_covered")
            except (json.JSONDecodeError, OSError):
                pass

        return TestResults(passed=exit_code == 0, output=output, coverage_pct=coverage_pct)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_lint(repo_path: str, file_changes: list[FileChange]) -> LintResults:
    client = _get_docker_client()
    _ensure_image(client)
    workdir = _materialize_workspace(repo_path, file_changes, [])
    try:
        # Bind mounts from a Windows host can present every file as executable to
        # the Linux container regardless of host permissions -- normalize inside
        # the container before linting so ruff doesn't flag spurious EXE002s.
        lint_command = [
            "sh",
            "-c",
            "find . -type f -exec chmod 644 {} + && ruff check --output-format=json .",
        ]
        exit_code, output = _run_in_container(client, workdir, lint_command)

        issues: list[str] = []
        try:
            parsed = json.loads(output)
            issues = [
                f"{item['filename']}:{item['location']['row']}: {item['code']} {item['message']}"
                for item in parsed
            ]
        except (json.JSONDecodeError, KeyError, TypeError):
            if exit_code != 0 and output.strip():
                issues = [output.strip()]

        return LintResults(passed=exit_code == 0, issues=issues)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
