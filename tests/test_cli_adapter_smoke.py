import os
from pathlib import Path

import pytest

from adapters.cli_adapter.run import run
from tools import github_tools

# `-m integration` alone is not enough to run these -- they do real, costly
# things (a real Anthropic call; test_cli_adapter_opens_pr_end_to_end opens a
# real PR against a real repo), so someone running `pytest -m integration` on
# reflex (e.g. muscle memory from another project) must not trigger that by
# accident. Requires explicitly opting in via this env var too.
_INTEGRATION_OPT_IN = "AGENTDEV_RUN_INTEGRATION_TESTS"
requires_integration_opt_in = pytest.mark.skipif(
    os.environ.get(_INTEGRATION_OPT_IN) != "1",
    reason=f"set {_INTEGRATION_OPT_IN}=1 to opt in -- this makes real, costly calls (see module docstring)",
)

# Test-only fixture: an empty target repo plus a real, substantial requirement
# to scaffold into it, so the end-to-end assertions below (build, run tests,
# open a PR) have something non-trivial to exercise. Deliberately local to
# this test file -- production code (adapters/cli_adapter/run.py) must never
# default to a canned requirement/repo, see RequirementError there.
_IOS_SCAFFOLD_REPO_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "ios_scaffold"
_IOS_SCAFFOLD_REQUIREMENT = (
    "Create a new iOS application project at the repository root. The project must build and "
    "run on the iOS Simulator and execute its unit tests successfully from the command line. "
    "No feature work is in scope; this requirement covers project setup only.\n"
    "\n"
    "Specifications\n"
    "App name: <AppName>\n"
    "Bundle identifier: <com.yourorg.appname>\n"
    "Minimum deployment target: iOS 17.0\n"
    "Language: Swift\n"
    "One app target and one unit test target\n"
    "The app's initial screen renders the static text Scaffold OK\n"
    "\n"
    "Acceptance criteria\n"
    "xcodebuild -scheme <AppName> -destination 'platform=iOS Simulator,name=iPhone 16' build "
    "exits with code 0.\n"
    "xcodebuild -scheme <AppName> -destination 'platform=iOS Simulator,name=iPhone 16' test "
    "exits with code 0 and runs at least one test that makes a real assertion.\n"
    "The built .app installs and launches on a simulator via xcrun simctl without crashing, "
    "and displays Scaffold OK.\n"
    "Bundle identifier and deployment target in the built product match the values specified "
    "above.\n"
    ".gitignore excludes DerivedData/, *.xcuserstate, and .DS_Store.\n"
    "No absolute filesystem paths from the build environment appear in any committed file.\n"
    "README.md documents the exact commands to build, test, and run the app.\n"
    "\n"
    "Out of scope\n"
    "Launch screen configuration, navigation, authentication, third-party dependencies, "
    "CI workflow files.\n"
    "\n"
    "Definition of done\n"
    "All acceptance criteria verified by running the stated commands, with output included "
    "in the PR description."
)


@pytest.mark.integration
@requires_integration_opt_in
def test_cli_adapter_runs_end_to_end():
    """Requires Docker running and a real ANTHROPIC_API_KEY. Excluded from the
    default `pytest` run -- run explicitly with `pytest -m integration` AND
    AGENTDEV_RUN_INTEGRATION_TESTS=1. Interactive: `run()` now always stops at
    the plan-approval prompt, so this needs a `y` typed at the terminal (or
    piped stdin) before it will proceed to code generation."""
    run(requirement=_IOS_SCAFFOLD_REQUIREMENT, repo_path=str(_IOS_SCAFFOLD_REPO_PATH), base_branch="main")


@pytest.mark.integration
@requires_integration_opt_in
@pytest.mark.skipif(
    not github_tools.is_configured(),
    reason="GitHub is not configured (see .agentdev.toml / agentdev doctor) -- can't test the GitHub PR phase.",
)
def test_cli_adapter_opens_pr_end_to_end():
    """Requires Docker, a real ANTHROPIC_API_KEY, and a real GITHUB_TOKEN/GITHUB_REPO
    pointing at a disposable test repo. Excluded from the default `pytest` run and
    skipped even under `-m integration` until GitHub credentials are configured AND
    AGENTDEV_RUN_INTEGRATION_TESTS=1 is set -- this opens a real PR. Interactive: see
    test_cli_adapter_runs_end_to_end's note on the plan-approval prompt."""
    run(
        requirement=_IOS_SCAFFOLD_REQUIREMENT,
        repo_path=str(_IOS_SCAFFOLD_REPO_PATH),
        base_branch="main",
        ticket_id="AD-101",
    )
