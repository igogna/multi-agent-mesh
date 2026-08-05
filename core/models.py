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


class RunState(BaseModel):
    """The full working state for one requirement -> PR run.
    This is what orchestration adapters pass around; it's just data."""

    requirement: str
    repo_url: str
    base_branch: str
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
