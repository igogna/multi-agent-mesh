from pydantic import BaseModel
from typing import Optional, Literal


class Plan(BaseModel):
    summary: str
    files_to_touch: list[str]
    acceptance_criteria: list[str]
    edge_cases: list[str]


class FileChange(BaseModel):
    path: str
    diff: str
    full_content: str


class TestFile(BaseModel):
    path: str
    content: str


class TestResults(BaseModel):
    passed: bool
    output: str
    coverage_pct: Optional[float] = None


class LintResults(BaseModel):
    passed: bool
    issues: list[str]


class ReviewIssue(BaseModel):
    file: str
    line: Optional[int]
    issue: str
    suggested_fix: str


class ReviewResult(BaseModel):
    status: Literal["approved", "changes_requested"]
    issues: list[ReviewIssue] = []


class HumanReviewIssue(BaseModel):
    author: str
    body: str
    review_id: Optional[int] = None
    comment_id: Optional[int] = None
    path: Optional[str] = None
    line: Optional[int] = None


class HumanReviewResult(BaseModel):
    """Result of reading a PR's actual GitHub review state -- a plain REST
    read, distinct from ReviewResult which is the internal LLM's opinion."""

    pr_state: Literal["open", "merged", "closed_unmerged"]
    verdict: Literal["approved", "changes_requested", "commented_only", "no_review_yet"]
    latest_review_id: Optional[int] = None
    reviewers: dict[str, Literal["approved", "changes_requested", "commented"]] = {}
    feedback_items: list[HumanReviewIssue] = []
    new_comment_ids: list[int] = []


class RunState(BaseModel):
    """The full working state for one requirement -> PR run.
    This is what orchestration adapters pass around; it's just data."""

    requirement: str
    repo_url: str
    base_branch: str
    ticket_id: Optional[str] = None
    working_branch: Optional[str] = None
    plan: Optional[Plan] = None
    file_changes: list[FileChange] = []
    test_files: list[TestFile] = []
    test_results: Optional[TestResults] = None
    lint_results: Optional[LintResults] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    review_result: Optional[ReviewResult] = None
    iteration: int = 0
    max_iterations: int = 3
    skip_tests: bool = False

    # Transient feedback for the next generate() call -- set by whichever step
    # (tests/lint or review) just failed, read once by generate then cleared.
    # Lets graph nodes (which only communicate via state) reproduce the same
    # "what feedback for this retry" flow the plain while-loop threads through
    # a local variable.
    feedback: Optional[str] = None

    # LangGraph plan-approval gate: a human approves or rejects the Plan
    # requirement_analyzer.analyze() produces before any code is generated.
    # Distinct budget from max_iterations, same shape as the human PR-review
    # budget below -- this is a separate approval loop, not a code-retry loop.
    plan_decision: Optional[Literal["approved", "rejected"]] = None
    plan_feedback: Optional[str] = None
    plan_review_rounds: int = 0
    max_plan_review_rounds: int = 3

    # Human (GitHub) review tracking -- distinct budget/state from the
    # automated LLM review above, since it's gated by human attention across
    # separate check_pr invocations rather than same-process retries.
    human_review: Optional[HumanReviewResult] = None
    human_review_state: Optional[Literal["pending", "merged", "closed_unmerged"]] = None
    last_processed_review_id: Optional[int] = None
    human_review_rounds: int = 0
    max_human_review_rounds: int = 3
    human_has_reviewed: bool = False
