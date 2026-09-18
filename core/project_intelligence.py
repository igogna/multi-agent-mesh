"""The curated Project Intelligence data model and its governance rule.

A Fact is never just content -- it carries a status and provenance so a
downstream consumer (a future ContextRouter) can tell an established fact
from a guess. The rule that matters most: an LLM raising its own confidence
score is never sufficient to promote a fact to `verified` -- only
independent evidence is (a Graphify graph edge, a passing test, explicit
human sign-off, or direct doc-text extraction). This module is the one place
that rule is enforced in code rather than left to convention.
"""

import hashlib
import time
from typing import Literal

from pydantic import BaseModel

FactType = Literal[
    "architecture",
    "component",
    "business_rule",
    "api_contract",
    "dependency",
    "decision",
    "requirement",
    "constraint",
    "known_issue",
    "technical_debt",
    "implementation_note",
]
FactStatus = Literal["verified", "inferred", "unknown", "stale"]
FactSource = Literal["graphify", "llm_inference", "human", "test", "doc"]
Trigger = Literal["graphify_edge", "passing_test", "human_signoff", "doc_extraction", "initial_extraction"]

# The only triggers independent enough to promote a fact to verified. An LLM
# raising its own confidence is deliberately not in this set.
_ALLOWED_VERIFIED_TRIGGERS = {"graphify_edge", "passing_test", "human_signoff", "doc_extraction"}


class Transition(BaseModel):
    from_status: FactStatus
    to_status: FactStatus
    trigger: Trigger
    at: str
    detail: str | None = None


class Fact(BaseModel):
    id: str
    type: FactType
    content: str
    status: FactStatus
    confidence: float
    evidence: list[str]
    source: FactSource
    created_at: str
    updated_at: str
    last_verified: str | None = None
    history: list[Transition] = []


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def make_fact_id(type: FactType, content: str) -> str:
    """Deterministic id (type prefix + content hash) so re-extracting the
    same underlying fact on a repeat bootstrap overwrites the same file
    instead of accumulating duplicates."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{type}-{digest}"


def new_fact(
    *,
    type: FactType,
    content: str,
    status: FactStatus,
    confidence: float,
    evidence: list[str],
    source: FactSource,
    trigger: Trigger,
    detail: str | None = None,
) -> Fact:
    """Creates a Fact directly at `status`. The verified-trigger check applies
    here too, not just in promote_to_verified -- a fact can reach verified
    either by being promoted later or by being created verified from
    trustworthy data (e.g. a Graphify structural edge), but never without a
    valid trigger either way."""
    if status == "verified" and trigger not in _ALLOWED_VERIFIED_TRIGGERS:
        raise ValueError(
            f"cannot create a verified fact via trigger {trigger!r}: "
            "not independent evidence -- an LLM's own confidence is never sufficient"
        )
    now = _now()
    return Fact(
        id=make_fact_id(type, content),
        type=type,
        content=content,
        status=status,
        confidence=confidence,
        evidence=evidence,
        source=source,
        created_at=now,
        updated_at=now,
        last_verified=now if status == "verified" else None,
        history=[Transition(from_status="unknown", to_status=status, trigger=trigger, at=now, detail=detail)],
    )


def promote_to_verified(fact: Fact, trigger: Trigger, detail: str | None = None) -> Fact:
    """The governance rule: a fact may only move to verified when backed by
    independent evidence. Raises if `trigger` isn't one of the allowed set --
    in particular, there is no trigger value for "the LLM is more confident
    now"."""
    if trigger not in _ALLOWED_VERIFIED_TRIGGERS:
        raise ValueError(
            f"cannot promote fact {fact.id!r} to verified via trigger {trigger!r}: "
            "not independent evidence -- an LLM's own confidence is never sufficient"
        )
    now = _now()
    updated = fact.model_copy(
        update={
            "status": "verified",
            "updated_at": now,
            "last_verified": now,
            "history": [
                *fact.history,
                Transition(from_status=fact.status, to_status="verified", trigger=trigger, at=now, detail=detail),
            ],
        }
    )
    return updated
