"""Per-language sandbox configuration: how to detect a project's language
from its file listing, which Docker image builds its toolchain, what
commands run its tests/lint, and how to parse each tool's lint output into
plain strings. Adding a new language means adding one LanguageConfig entry
here plus its Dockerfile under sandbox/<id>/ -- tools/sandbox_tools.py and
core/test_generator.py never need a per-language branch of their own.

Detection only looks at file *names* already returned by
tools/repo_context.list_files (capped at 200, alphabetical) -- a marker file
that exists but sorts past that cap (large monorepos, deeply nested Android
manifests) can be missed. Good enough for a single-language project, which is
what this targets; a real monorepo with multiple stacks needs a fancier
per-directory detection this doesn't attempt.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class LanguageConfig:
    id: str
    display_name: str
    # Fed into the test-generation prompt, e.g. "pytest" or "Go's built-in
    # testing package" -- see core/test_generator.py.
    test_framework_hint: str
    # Directory name under sandbox/ holding this language's Dockerfile.
    dockerfile_dir: str
    # Marker basenames that identify this language, checked anywhere in the
    # file listing (not just the repo root).
    markers: tuple[str, ...]
    test_command: list[str]
    # None means "no lint step for this language yet" -- run_lint short-
    # circuits without starting a container.
    lint_command: list[str] | None = None
    parse_lint: Callable[[str, int], list[str]] | None = None


def default_parse_lint(output: str, exit_code: int) -> list[str]:
    if exit_code == 0:
        return []
    return [output.strip()] if output.strip() else [f"lint command exited {exit_code} with no output"]


def _parse_ruff_json(output: str, exit_code: int) -> list[str]:
    try:
        parsed = json.loads(output)
        return [
            f"{item['filename']}:{item['location']['row']}: {item['code']} {item['message']}"
            for item in parsed
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        return default_parse_lint(output, exit_code)


def _parse_eslint_json(output: str, exit_code: int) -> list[str]:
    try:
        parsed = json.loads(output)
        issues = []
        for file_result in parsed:
            for msg in file_result.get("messages", []):
                rule = msg.get("ruleId") or "error"
                issues.append(f"{file_result['filePath']}:{msg.get('line')}: {rule} {msg.get('message')}")
        return issues
    except (json.JSONDecodeError, KeyError, TypeError):
        return default_parse_lint(output, exit_code)


def _parse_go_vet(output: str, exit_code: int) -> list[str]:
    if exit_code == 0:
        return []
    return [line for line in output.strip().splitlines() if line.strip()]


# Order matters: the first language whose markers are found wins. Android is
# checked before the generic JVM entry since an Android project also has a
# build.gradle and would otherwise be (mis)built as a plain Java/Kotlin
# project -- missing the Android SDK its own build actually needs.
LANGUAGES: list[LanguageConfig] = [
    LanguageConfig(
        id="android",
        display_name="Android (Kotlin/Java, Gradle)",
        test_framework_hint=(
            "JUnit4 Android unit tests (Kotlin), run via the Gradle wrapper's testDebugUnitTest "
            "task -- local JVM tests only, no emulator/instrumented (androidTest) tests"
        ),
        dockerfile_dir="android",
        markers=("AndroidManifest.xml",),
        test_command=["sh", "-c", "sh gradlew testDebugUnitTest --no-daemon -q || sh gradlew test --no-daemon -q"],
        lint_command=None,  # android lint needs the full SDK + a real module graph; out of scope for now
    ),
    LanguageConfig(
        id="jvm",
        display_name="Java/Kotlin (Gradle or Maven)",
        test_framework_hint="JUnit tests, in whichever of Java or Kotlin the existing source already uses",
        dockerfile_dir="jvm",
        markers=("build.gradle", "build.gradle.kts", "pom.xml"),
        test_command=[
            "sh",
            "-c",
            "if [ -f gradlew ]; then sh gradlew test --no-daemon -q; "
            "elif [ -f pom.xml ]; then mvn -q -B test; "
            "else echo 'No gradlew or pom.xml found' >&2; exit 1; fi",
        ],
        lint_command=None,  # no single linter is standard across Java/Kotlin/Gradle/Maven projects
    ),
    LanguageConfig(
        id="node",
        display_name="Node.js/TypeScript",
        test_framework_hint="whatever test runner `npm test` invokes in this project (Jest/Vitest/Mocha, etc.)",
        dockerfile_dir="node",
        markers=("package.json",),
        test_command=["sh", "-c", "npm ci --silent 2>/dev/null || npm install --silent; npm test --silent"],
        lint_command=["sh", "-c", "npx --yes eslint . --format json 2>/dev/null || echo '[]'"],
        parse_lint=_parse_eslint_json,
    ),
    LanguageConfig(
        id="go",
        display_name="Go",
        test_framework_hint="Go's built-in testing package (table-driven tests in *_test.go files)",
        dockerfile_dir="go",
        markers=("go.mod",),
        test_command=["sh", "-c", "go test ./... -v"],
        lint_command=["sh", "-c", "go vet ./... 2>&1"],
        parse_lint=_parse_go_vet,
    ),
    LanguageConfig(
        id="python",
        display_name="Python",
        test_framework_hint="pytest",
        dockerfile_dir="python",
        markers=("requirements.txt", "pyproject.toml", "setup.py", "Pipfile"),
        test_command=[
            "sh",
            "-c",
            "pip install -q -r requirements.txt 2>/dev/null; pip install -q -e . 2>/dev/null; "
            "pytest --cov=. --cov-report=json:coverage.json -q",
        ],
        lint_command=["sh", "-c", "find . -type f -exec chmod 644 {} + && ruff check --output-format=json ."],
        parse_lint=_parse_ruff_json,
    ),
]

# Repos with none of the above markers (e.g. a brand-new/empty project)
# default to Python -- the only stack this tool supported before multi-
# language detection existed, so this preserves that behavior rather than
# erroring out.
_DEFAULT = LANGUAGES[-1]
assert _DEFAULT.id == "python"


def detect_language(files: list[str]) -> LanguageConfig:
    basenames = {Path(f).name for f in files}
    for lang in LANGUAGES:
        if any(marker in basenames for marker in lang.markers):
            return lang
    return _DEFAULT
