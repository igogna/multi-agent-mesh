import os

import pytest

from adapters.cli_adapter.run import DEFAULT_REQUIREMENT, IOS_SCAFFOLD_REPO_PATH, run
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


@pytest.mark.integration
@requires_integration_opt_in
def test_cli_adapter_runs_end_to_end():
    """Requires Docker running and a real ANTHROPIC_API_KEY. Excluded from the
    default `pytest` run -- run explicitly with `pytest -m integration` AND
    AGENTDEV_RUN_INTEGRATION_TESTS=1."""
    run(requirement=DEFAULT_REQUIREMENT, repo_path=str(IOS_SCAFFOLD_REPO_PATH), base_branch="main")


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
    AGENTDEV_RUN_INTEGRATION_TESTS=1 is set -- this opens a real PR."""
    run(
        requirement=DEFAULT_REQUIREMENT,
        repo_path=str(IOS_SCAFFOLD_REPO_PATH),
        base_branch="main",
        ticket_id="AD-101",
    )
