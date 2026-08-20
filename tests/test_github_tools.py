from unittest.mock import MagicMock

import git
import pytest

import tools.github_tools as github_tools
from core.models import FileChange


@pytest.fixture
def fake_github_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_REPO", "acme/widgets")


def test_is_configured_false_when_placeholders(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "<<GITHUB_TOKEN>>")
    monkeypatch.setenv("GITHUB_REPO", "<<GITHUB_REPO>>")
    assert github_tools.is_configured() is False


def test_is_configured_true_when_set(fake_github_env):
    assert github_tools.is_configured() is True


def test_open_pr_creates_new_pr_when_none_exists(fake_github_env, monkeypatch):
    mock_pr = MagicMock(number=42, html_url="https://github.com/acme/widgets/pull/42")
    mock_repo = MagicMock()
    mock_repo.owner.login = "acme"
    mock_repo.get_pulls.return_value = []
    mock_repo.create_pull.return_value = mock_pr

    mock_client = MagicMock()
    mock_client.get_repo.return_value = mock_repo
    monkeypatch.setattr(github_tools, "_get_github_client", lambda token: mock_client)

    number, url = github_tools.open_pr("acme/widgets", "feature/AD-1", "main", "title", "body")

    assert (number, url) == (42, "https://github.com/acme/widgets/pull/42")
    mock_repo.create_pull.assert_called_once_with(
        title="title", body="body", head="feature/AD-1", base="main"
    )


def test_open_pr_returns_existing_pr_without_creating_duplicate(fake_github_env, monkeypatch):
    existing_pr = MagicMock(number=7, html_url="https://github.com/acme/widgets/pull/7")
    mock_repo = MagicMock()
    mock_repo.owner.login = "acme"
    mock_repo.get_pulls.return_value = [existing_pr]

    mock_client = MagicMock()
    mock_client.get_repo.return_value = mock_repo
    monkeypatch.setattr(github_tools, "_get_github_client", lambda token: mock_client)

    number, url = github_tools.open_pr("acme/widgets", "feature/AD-1", "main", "title", "body")

    assert (number, url) == (7, "https://github.com/acme/widgets/pull/7")
    mock_repo.create_pull.assert_not_called()


def test_post_review_comment_posts_to_pr(fake_github_env, monkeypatch):
    mock_pr = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_pull.return_value = mock_pr

    mock_client = MagicMock()
    mock_client.get_repo.return_value = mock_repo
    monkeypatch.setattr(github_tools, "_get_github_client", lambda token: mock_client)

    github_tools.post_review_comment("acme/widgets", 42, "looks good")

    mock_pr.create_issue_comment.assert_called_once_with("looks good")


def test_open_pr_raises_without_config(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    with pytest.raises(github_tools.GitHubConfigError):
        github_tools.open_pr("acme/widgets", "branch", "main", "t", "b")


def _mock_review(login: str, state: str, review_id: int, body: str = ""):
    review = MagicMock(id=review_id, state=state, body=body)
    review.user.login = login
    return review


def _mock_comment(id_: int, login: str, body: str, review_id: int | None = None, path: str | None = None, line=None):
    comment = MagicMock(id=id_, body=body, pull_request_review_id=review_id, path=path, line=line)
    comment.user.login = login
    return comment


@pytest.fixture
def mock_gh_client(monkeypatch, fake_github_env):
    mock_client = MagicMock()
    mock_client.get_user.return_value.login = "agent-bot"
    monkeypatch.setattr(github_tools, "_get_github_client", lambda token: mock_client)
    github_tools._LOGIN_CACHE.clear()
    return mock_client


def test_get_pr_status_merged_short_circuits_before_reading_reviews(mock_gh_client):
    mock_pr = MagicMock(merged=True)
    mock_gh_client.get_repo.return_value.get_pull.return_value = mock_pr

    result = github_tools.get_pr_status("acme/widgets", 42)

    assert result.pr_state == "merged"
    mock_pr.get_reviews.assert_not_called()


def test_get_pr_status_closed_unmerged(mock_gh_client):
    mock_pr = MagicMock(merged=False, state="closed")
    mock_gh_client.get_repo.return_value.get_pull.return_value = mock_pr

    result = github_tools.get_pr_status("acme/widgets", 42)

    assert result.pr_state == "closed_unmerged"


def test_get_pr_status_no_review_yet(mock_gh_client):
    mock_pr = MagicMock(merged=False, state="open")
    mock_pr.get_reviews.return_value = []
    mock_pr.get_issue_comments.return_value = []
    mock_gh_client.get_repo.return_value.get_pull.return_value = mock_pr

    result = github_tools.get_pr_status("acme/widgets", 42)

    assert result.pr_state == "open"
    assert result.verdict == "no_review_yet"


def test_get_pr_status_changes_requested_collects_body_and_inline_comments(mock_gh_client):
    mock_pr = MagicMock(merged=False, state="open")
    mock_pr.get_reviews.return_value = [
        _mock_review("alice", "CHANGES_REQUESTED", review_id=10, body="Please fix the edge case."),
    ]
    mock_pr.get_review_comments.return_value = [
        _mock_comment(100, "alice", "off by one here", review_id=10, path="core/ops.py", line=5),
        _mock_comment(101, "bob", "unrelated old comment", review_id=999),
    ]
    mock_pr.get_issue_comments.return_value = []
    mock_gh_client.get_repo.return_value.get_pull.return_value = mock_pr

    result = github_tools.get_pr_status("acme/widgets", 42)

    assert result.verdict == "changes_requested"
    assert result.latest_review_id == 10
    assert result.reviewers == {"alice": "changes_requested"}
    bodies = {(item.author, item.body) for item in result.feedback_items}
    assert ("alice", "Please fix the edge case.") in bodies
    assert ("alice", "off by one here") in bodies
    assert len(result.feedback_items) == 2  # bob's comment on a different review is excluded


def test_get_pr_status_latest_review_per_user_wins(mock_gh_client):
    mock_pr = MagicMock(merged=False, state="open")
    mock_pr.get_reviews.return_value = [
        _mock_review("alice", "CHANGES_REQUESTED", review_id=1),
        _mock_review("alice", "APPROVED", review_id=2),
    ]
    mock_pr.get_issue_comments.return_value = []
    mock_gh_client.get_repo.return_value.get_pull.return_value = mock_pr

    result = github_tools.get_pr_status("acme/widgets", 42)

    assert result.verdict == "approved"
    assert result.reviewers == {"alice": "approved"}


def test_get_pr_status_multi_reviewer_disagreement_blocks_on_any_changes_requested(mock_gh_client):
    mock_pr = MagicMock(merged=False, state="open")
    mock_pr.get_reviews.return_value = [
        _mock_review("alice", "APPROVED", review_id=1),
        _mock_review("bob", "CHANGES_REQUESTED", review_id=2),
    ]
    mock_pr.get_review_comments.return_value = []
    mock_pr.get_issue_comments.return_value = []
    mock_gh_client.get_repo.return_value.get_pull.return_value = mock_pr

    result = github_tools.get_pr_status("acme/widgets", 42)

    assert result.verdict == "changes_requested"
    assert result.reviewers == {"alice": "approved", "bob": "changes_requested"}


def test_get_pr_status_ignores_bot_own_reviews_and_comments(mock_gh_client):
    mock_pr = MagicMock(merged=False, state="open")
    mock_pr.get_reviews.return_value = [
        _mock_review("agent-bot", "CHANGES_REQUESTED", review_id=1),
    ]
    bot_comment = MagicMock(id=200, body="Automated review: ...")
    bot_comment.user.login = "agent-bot"
    mock_pr.get_issue_comments.return_value = [bot_comment]
    mock_gh_client.get_repo.return_value.get_pull.return_value = mock_pr

    result = github_tools.get_pr_status("acme/widgets", 42)

    assert result.verdict == "no_review_yet"
    assert result.reviewers == {}
    assert result.new_comment_ids == []


@pytest.fixture
def bare_origin(tmp_path):
    bare_path = tmp_path / "origin.git"
    git.Repo.init(bare_path, bare=True)

    seed_path = tmp_path / "seed"
    seed = git.Repo.init(seed_path)
    (seed_path / "README.md").write_text("hello\n", encoding="utf-8")
    seed.index.add(["README.md"])
    seed.index.commit("initial commit")
    seed.git.branch("-M", "main")
    seed.create_remote("origin", str(bare_path))
    seed.remotes.origin.push("main:main")

    git.Repo(bare_path).git.symbolic_ref("HEAD", "refs/heads/main")
    return bare_path


def test_create_branch_and_commit_and_push_against_local_repo(
    fake_github_env, monkeypatch, bare_origin, tmp_path
):
    clone_dir = tmp_path / "clone"

    def fake_local_clone(repo_slug, token):
        if clone_dir.exists():
            return git.Repo(clone_dir)
        return git.Repo.clone_from(str(bare_origin), clone_dir)

    monkeypatch.setattr(github_tools, "_local_clone", fake_local_clone)

    branch = github_tools.create_branch("acme/widgets", "main", "feature/AD-1")
    assert branch == "feature/AD-1"

    file_changes = [FileChange(path="hello.txt", diff="", full_content="hi\n")]
    github_tools.commit_and_push("acme/widgets", "feature/AD-1", file_changes, [], "AD-1: add hello")

    check_dir = tmp_path / "check"
    check_repo = git.Repo.clone_from(str(bare_origin), check_dir)
    check_repo.git.checkout("feature/AD-1")
    assert (check_dir / "hello.txt").read_text(encoding="utf-8") == "hi\n"

    # A second push with identical content must not raise (is_dirty guard).
    github_tools.commit_and_push("acme/widgets", "feature/AD-1", file_changes, [], "AD-1: add hello")
