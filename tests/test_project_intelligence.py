import pytest

from core.project_intelligence import Fact, new_fact, promote_to_verified


def _inferred_fact() -> Fact:
    return new_fact(
        type="dependency",
        content="A calls B",
        status="inferred",
        confidence=0.5,
        evidence=["a.py:1"],
        source="graphify",
        trigger="initial_extraction",
    )


def test_new_fact_records_initial_transition():
    fact = _inferred_fact()
    assert fact.status == "inferred"
    assert len(fact.history) == 1
    assert fact.history[0].to_status == "inferred"
    assert fact.last_verified is None


@pytest.mark.parametrize("trigger", ["graphify_edge", "passing_test", "human_signoff", "doc_extraction"])
def test_promote_to_verified_accepts_independent_evidence(trigger):
    fact = promote_to_verified(_inferred_fact(), trigger=trigger)
    assert fact.status == "verified"
    assert fact.last_verified is not None
    assert fact.history[-1].trigger == trigger
    assert fact.history[-1].from_status == "inferred"
    assert fact.history[-1].to_status == "verified"


def test_promote_to_verified_rejects_llm_confidence_as_a_trigger():
    with pytest.raises(ValueError):
        promote_to_verified(_inferred_fact(), trigger="initial_extraction")


def test_promote_to_verified_rejects_unknown_trigger():
    with pytest.raises(ValueError):
        promote_to_verified(_inferred_fact(), trigger="llm_says_so")


def test_new_fact_rejects_creating_verified_without_independent_trigger():
    with pytest.raises(ValueError):
        new_fact(
            type="component",
            content="X exists",
            status="verified",
            confidence=0.99,
            evidence=[],
            source="llm_inference",
            trigger="initial_extraction",
        )


def test_new_fact_allows_verified_creation_with_valid_trigger():
    fact = new_fact(
        type="component",
        content="X is defined in x.py",
        status="verified",
        confidence=1.0,
        evidence=["x.py:1"],
        source="graphify",
        trigger="graphify_edge",
    )
    assert fact.status == "verified"
    assert fact.last_verified is not None


def test_promote_to_verified_does_not_mutate_the_original_fact():
    original = _inferred_fact()
    promote_to_verified(original, trigger="passing_test")
    assert original.status == "inferred"
    assert len(original.history) == 1
