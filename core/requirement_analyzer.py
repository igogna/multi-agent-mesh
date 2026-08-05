from core.models import Plan


def analyze(requirement: str, repo_context: dict) -> Plan:
    return Plan(
        summary="stub: dummy plan",
        files_to_touch=["stub_file.py"],
        acceptance_criteria=["stub acceptance criterion"],
        edge_cases=["stub edge case"],
    )
