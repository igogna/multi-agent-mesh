from core.knowledge_extractor import extract_from_docs, extract_from_graph, seed_from_human_answers
from core.knowledge_extractor import HumanAnswer


def test_extract_from_graph_empty_input():
    assert extract_from_graph({}) == []


def test_extract_from_graph_maps_nodes_to_verified_component_facts():
    graph_data = {
        "nodes": [
            {"id": "core_models_plan", "label": "Plan", "source_file": "core/models.py", "source_location": "L5"}
        ],
        "links": [],
    }
    facts = extract_from_graph(graph_data)
    assert len(facts) == 1
    fact = facts[0]
    assert fact.type == "component"
    assert fact.status == "verified"
    assert fact.source == "graphify"
    assert fact.evidence == ["core/models.py:L5"]


def test_extract_from_graph_extracted_edge_is_verified():
    graph_data = {
        "nodes": [],
        "links": [
            {
                "source": "a",
                "target": "b",
                "relation": "calls",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": "a.py",
                "source_location": "L1",
            }
        ],
    }
    facts = extract_from_graph(graph_data)
    assert len(facts) == 1
    assert facts[0].status == "verified"
    assert facts[0].type == "dependency"


def test_extract_from_graph_inferred_edge_stays_inferred():
    graph_data = {
        "nodes": [],
        "links": [
            {
                "source": "a",
                "target": "b",
                "relation": "uses",
                "confidence": "INFERRED",
                "confidence_score": 0.6,
                "source_file": "a.py",
                "source_location": "L1",
            }
        ],
    }
    facts = extract_from_graph(graph_data)
    assert len(facts) == 1
    assert facts[0].status == "inferred"


def test_extract_from_docs_empty_input_makes_no_llm_call():
    facts, tokens = extract_from_docs({})
    assert facts == []
    assert tokens == 0


def test_seed_from_human_answers_skips_blank_answers():
    answers = [
        HumanAnswer(type="constraint", question="How does auth work?", answer=""),
        HumanAnswer(type="constraint", question="How is this deployed?", answer="Docker + ECS"),
    ]
    facts = seed_from_human_answers(answers)
    assert len(facts) == 1
    assert facts[0].source == "human"
    assert facts[0].status == "verified"
    assert "Docker + ECS" in facts[0].content
