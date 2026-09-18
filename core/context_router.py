"""Picks which stored ProjectIntelligence facts are relevant to a given
requirement, before the planning step sees them. Deterministic keyword
overlap, not an LLM call -- see the plan doc for why: every other core/*.py
module treats "one deliberate LLM call, otherwise pure Python" as the norm,
and a second LLM call here would add latency/cost to every run against a
bootstrapped repo for a job simple overlap scoring can do as a first pass.
This also satisfies "the LLM must never receive the entire ProjectIntelligence
store": only facts with real keyword overlap survive, capped at `limit`.
"""

import re

from core.project_intelligence import Fact

DEFAULT_LIMIT = 30

# Common English + requirement-boilerplate words ("add", "fix", "update"...)
# excluded from keyword extraction so they don't cause noisy over-matching --
# not exhaustive, easy to extend.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "to",
    "of", "in", "on", "at", "for", "with", "and", "or", "but", "not", "this",
    "that", "these", "those", "it", "its", "as", "by", "from", "if", "when",
    "should", "must", "will", "would", "can", "could", "add", "new", "update",
    "fix", "make", "ensure", "so", "than", "then",
}

_STATUS_PRIORITY = {"verified": 0, "inferred": 1, "stale": 2}


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}


def _score(requirement_keywords: set[str], fact: Fact) -> int:
    fact_text = f"{fact.content} {' '.join(fact.evidence)}".lower()
    return sum(1 for kw in requirement_keywords if kw in fact_text)


def select_relevant_facts(requirement: str, facts: list[Fact], limit: int = DEFAULT_LIMIT) -> list[Fact]:
    """Keyword-overlap ranking, capped at `limit`. Facts with zero overlap
    are dropped entirely (not just deprioritized) -- the planner should never
    see irrelevant noise. `stale` facts are excluded outright: per the Fact
    status model, they need revalidation before reuse (no code produces
    `stale` yet, but this stays correct once a future ContextValidator does).
    """
    keywords = _keywords(requirement)
    if not keywords:
        return []

    candidates = [f for f in facts if f.status != "stale"]
    scored = [(f, _score(keywords, f)) for f in candidates]
    scored = [(f, s) for f, s in scored if s > 0]
    scored.sort(key=lambda pair: (-pair[1], _STATUS_PRIORITY.get(pair[0].status, 9)))
    return [f for f, _ in scored[:limit]]
