import pytest

from adapters.cli_adapter.run import DEFAULT_REQUIREMENT, TOY_REPO_PATH, run
from tools import github_tools


@pytest.mark.integration
def test_cli_adapter_runs_end_to_end():
    """Requires Docker running and a real ANTHROPIC_API_KEY. Excluded from the
    default `pytest` run -- run explicitly with `pytest -m integration`."""
    run(requirement=DEFAULT_REQUIREMENT, repo_path=str(TOY_REPO_PATH), base_branch="main")


@pytest.mark.integration
@pytest.mark.skipif(
    not github_tools.is_configured(),
    reason="GITHUB_TOKEN/GITHUB_REPO not configured -- set them in .env to test the GitHub PR phase.",
)
def test_cli_adapter_opens_pr_end_to_end():
    """Requires Docker, a real ANTHROPIC_API_KEY, and a real GITHUB_TOKEN/GITHUB_REPO
    pointing at a disposable test repo. Excluded from the default `pytest` run and
    skipped even under `-m integration` until GitHub credentials are configured."""
    run(
        requirement=DEFAULT_REQUIREMENT,
        repo_path=str(TOY_REPO_PATH),
        base_branch="main",
        ticket_id="AD-101",
    )
