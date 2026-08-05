"""Plain Python loop wiring core/ functions together. Phase 1 stub only —
prints the sequence of calls it will make; the real requirement -> code ->
test -> retry loop is built in Phase 2.
"""

from core import (
    requirement_analyzer,
    code_generator,
    test_generator,
    review_agent,
    routing,
    pr_description,
)
from tools import github_tools, sandbox_tools, repo_context, secret_scan


def run(requirement: str, repo_url: str, base_branch: str) -> None:
    print(f"[stub] would analyze requirement: {requirement!r}")
    print("[stub] would call requirement_analyzer.analyze(...)")
    print("[stub] would call code_generator.generate(...)")
    print("[stub] would call test_generator.generate_tests(...)")
    print("[stub] would call sandbox_tools.run_tests(...) / run_lint(...)")
    print("[stub] would call routing.decide_after_tests(...)")
    print("[stub] would call secret_scan.scan(...) then github_tools.commit_and_push(...)")
    print("[stub] would call github_tools.open_pr(...) / pr_description.build_pr_body(...)")
    print("[stub] would call review_agent.review(...) then routing.decide_after_review(...)")
    print("[stub] would prompt for human merge-gate approval")


if __name__ == "__main__":
    run(
        requirement="stub requirement",
        repo_url="stub://repo",
        base_branch="main",
    )
