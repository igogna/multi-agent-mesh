from core.project_intelligence import new_fact
from core.requirement_analyzer import _format_facts_block


def test_format_facts_block_empty():
    assert _format_facts_block([]) == ""


def test_format_facts_block_includes_status_and_content():
    fact = new_fact(
        type="component",
        content="SandboxRunner runs tests in Docker",
        status="verified",
        confidence=1.0,
        evidence=["tools/sandbox_tools.py:L12"],
        source="graphify",
        trigger="graphify_edge",
    )
    block = _format_facts_block([fact])
    assert "[verified]" in block
    assert "SandboxRunner runs tests in Docker" in block
    assert "tools/sandbox_tools.py:L12" in block
