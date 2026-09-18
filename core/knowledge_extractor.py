"""Turns Graphify structural output and verified doc text into
ProjectIntelligence Facts. Two independent sources, both immediately
`verified` -- never `inferred` -- because both are traceable to something
that isn't the LLM's own opinion: a Graphify graph edge/node, or the literal
text of a document a human wrote. Anything neither source can confirm is
simply never emitted as a Fact here (unknown is the absence of an entry, not
a stored placeholder) -- see core/project_intelligence.py for the status
model this builds on.
"""

from typing import Literal

from pydantic import BaseModel

from core._llm import get_client, get_model
from core.project_intelligence import Fact, FactType, new_fact

_DOC_SYSTEM_PROMPT = """You are extracting durable business rules and constraints from a project's own \
documentation, for a coding agent's knowledge base about that project. Only extract facts that are \
explicitly stated in the text -- never infer, guess, or add anything not directly supported by the \
document. If a document contains no business rules or constraints, contribute nothing from it. Every \
extracted fact must cite which document it came from."""


class _ExtractedDocFact(BaseModel):
    type: Literal["business_rule", "constraint"]
    content: str
    source_file: str


class _DocExtractionOutput(BaseModel):
    facts: list[_ExtractedDocFact]


def extract_from_graph(graph_data: dict) -> list[Fact]:
    """graph_data is Graphify's graph.json shape (NetworkX node-link format):
    top-level "nodes" (id, label, source_file, source_location, ...) and
    "links" (source, target, relation, confidence: EXTRACTED|INFERRED|AMBIGUOUS,
    confidence_score, source_file, source_location). Graphify's own EXTRACTED
    tier maps to our verified; its INFERRED/AMBIGUOUS tiers stay inferred here
    too -- passing through Graphify's own honesty tagging rather than
    flattening everything to verified just because the source is Graphify."""
    facts: list[Fact] = []

    for node in graph_data.get("nodes", []):
        source_file = node.get("source_file")
        source_location = node.get("source_location")
        label = node.get("label") or node.get("id", "unknown")
        evidence = [f"{source_file}:{source_location}"] if source_file else [str(node.get("id", ""))]
        content = f"{label} is defined in {source_file}" if source_file else f"{label} exists in the codebase"
        facts.append(
            new_fact(
                type="component",
                content=content,
                status="verified",
                confidence=1.0,
                evidence=evidence,
                source="graphify",
                trigger="graphify_edge",
            )
        )

    for link in graph_data.get("links", []):
        relation = link.get("relation", "relates to")
        content = f"{link.get('source')} {relation} {link.get('target')}"
        source_file = link.get("source_file")
        source_location = link.get("source_location")
        evidence = [f"{source_file}:{source_location}"] if source_file else []
        confidence_score = link.get("confidence_score", 1.0)

        if link.get("confidence") == "EXTRACTED":
            facts.append(
                new_fact(
                    type="dependency",
                    content=content,
                    status="verified",
                    confidence=confidence_score,
                    evidence=evidence,
                    source="graphify",
                    trigger="graphify_edge",
                )
            )
        else:
            # Graphify itself flagged this edge INFERRED/AMBIGUOUS -- stays
            # inferred here; only an independent trigger (see
            # core/project_intelligence.py::promote_to_verified) can raise it.
            facts.append(
                new_fact(
                    type="dependency",
                    content=content,
                    status="inferred",
                    confidence=confidence_score,
                    evidence=evidence,
                    source="graphify",
                    trigger="initial_extraction",
                )
            )

    return facts


def extract_from_docs(docs: dict[str, str]) -> tuple[list[Fact], int]:
    """docs: {relative_path: file_content}. Returns (facts, total_tokens_used).
    A single batched call across every doc (cheaper than one call per file);
    empty input makes no LLM call at all."""
    if not docs:
        return [], 0

    docs_block = "\n\n".join(f"### {path}\n{content}" for path, content in docs.items())
    client = get_client()
    with client.messages.stream(
        model=get_model(),
        max_tokens=4096,
        thinking={"type": "disabled"},
        system=_DOC_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Project documentation:\n\n{docs_block}"}],
        output_format=_DocExtractionOutput,
    ) as stream:
        response = stream.get_final_message()

    tokens_used = response.usage.input_tokens + response.usage.output_tokens
    facts = [
        new_fact(
            type=item.type,
            content=item.content,
            status="verified",
            confidence=0.9,
            evidence=[item.source_file],
            source="doc",
            trigger="doc_extraction",
        )
        for item in response.parsed_output.facts
    ]
    return facts, tokens_used


class HumanAnswer(BaseModel):
    type: FactType
    question: str
    answer: str


def seed_from_human_answers(answers: list[HumanAnswer]) -> list[Fact]:
    """Turns a one-time bootstrap Q&A pass into verified, human-sourced
    Facts. No LLM involved -- the human is the evidence."""
    facts = []
    for item in answers:
        if not item.answer.strip():
            continue
        facts.append(
            new_fact(
                type=item.type,
                content=f"{item.question} {item.answer}",
                status="verified",
                confidence=1.0,
                evidence=["human bootstrap Q&A"],
                source="human",
                trigger="human_signoff",
            )
        )
    return facts
