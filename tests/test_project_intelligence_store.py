from core.project_intelligence import new_fact
from tools import project_intelligence_store as store


def _fact(type_="component", content="X exists"):
    return new_fact(
        type=type_,
        content=content,
        status="verified",
        confidence=1.0,
        evidence=["x.py:1"],
        source="graphify",
        trigger="graphify_edge",
    )


def test_category_for_type_mapping():
    assert store.category_for_type("decision") == "decisions"
    assert store.category_for_type("requirement") == "requirements"
    assert store.category_for_type("component") == "context"
    assert store.category_for_type("business_rule") == "context"


def test_save_and_load_round_trip(tmp_path):
    fact = _fact()
    path = store.save_fact(fact, tmp_path)
    assert path == tmp_path / ".project-intelligence" / "context" / f"{fact.id}.json"
    assert path.is_file()

    loaded = store.load_fact(fact.id, tmp_path)
    assert loaded == fact


def test_load_fact_searches_every_category(tmp_path):
    decision = _fact(type_="decision", content="use JWT")
    store.save_fact(decision, tmp_path)
    loaded = store.load_fact(decision.id, tmp_path)
    assert loaded is not None
    assert loaded.type == "decision"


def test_load_fact_returns_none_when_missing(tmp_path):
    assert store.load_fact("does-not-exist", tmp_path) is None


def test_list_facts_filters_by_category(tmp_path):
    store.save_fact(_fact(type_="component", content="A"), tmp_path)
    store.save_fact(_fact(type_="decision", content="B"), tmp_path)

    context_facts = store.list_facts("context", tmp_path)
    decision_facts = store.list_facts("decisions", tmp_path)
    all_facts = store.list_facts(project_root=tmp_path)

    assert len(context_facts) == 1
    assert len(decision_facts) == 1
    assert len(all_facts) == 2


def test_list_facts_empty_store_returns_empty(tmp_path):
    assert store.list_facts(project_root=tmp_path) == []
