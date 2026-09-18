from core.context_router import select_relevant_facts
from core.project_intelligence import new_fact


def _fact(content, status="verified", evidence=None, source="graphify", trigger="graphify_edge"):
    return new_fact(
        type="component",
        content=content,
        status=status,
        confidence=1.0,
        evidence=evidence or [],
        source=source,
        trigger=trigger,
    )


def test_empty_facts_returns_empty():
    assert select_relevant_facts("Add rate limiting to the API", []) == []


def test_empty_requirement_returns_empty():
    facts = [_fact("SandboxRunner runs tests in Docker")]
    assert select_relevant_facts("", facts) == []


def test_zero_overlap_facts_are_excluded():
    facts = [_fact("SandboxRunner runs tests in Docker")]
    assert select_relevant_facts("Rewrite the PR description formatting", facts) == []


def test_overlapping_fact_is_returned():
    facts = [_fact("SandboxRunner runs tests inside a Docker container")]
    result = select_relevant_facts("Make the sandbox tests run faster", facts)
    assert len(result) == 1
    assert result[0].content.startswith("SandboxRunner")


def test_evidence_text_also_counts_toward_overlap():
    facts = [_fact("Handles review comments", evidence=["tools/github_tools.py:L162"])]
    result = select_relevant_facts("Update tools/github_tools.py error handling", facts)
    assert len(result) == 1


def test_higher_overlap_ranks_first():
    # Scoring counts distinct keyword overlap, not term frequency, so this
    # needs two *different* requirement keywords -- one fact matching both
    # outranks one matching only one, regardless of list order.
    low = _fact("docker only")
    high = _fact("docker and sandbox together")
    result = select_relevant_facts("docker sandbox", [low, high])
    assert result[0] is high


def test_limit_is_enforced():
    facts = [_fact(f"docker fact {i}") for i in range(5)]
    result = select_relevant_facts("docker", facts, limit=2)
    assert len(result) == 2


def test_verified_ranked_before_inferred_at_equal_score():
    inferred = new_fact(
        type="dependency",
        content="docker relates to sandbox",
        status="inferred",
        confidence=0.5,
        evidence=[],
        source="graphify",
        trigger="initial_extraction",
    )
    verified = _fact("docker relates to sandbox too")
    result = select_relevant_facts("docker sandbox", [inferred, verified])
    assert result[0].status == "verified"


def test_stale_facts_are_never_returned():
    stale = _fact("docker sandbox info").model_copy(update={"status": "stale"})
    assert select_relevant_facts("docker sandbox", [stale]) == []
