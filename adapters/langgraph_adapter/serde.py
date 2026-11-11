"""Msgpack serialization configuration for LangGraph checkpointers.

LangGraph's JsonPlusSerializer enforces allowlists on custom types deserialized
from checkpoint msgpack data to prevent unsafe deserialization. Without an
explicit allowlist, it warns:
    Deserializing unregistered type core.models.X from checkpoint.
    This will be blocked in a future version.

This module registers all Pydantic model classes from core.models in
JsonPlusSerializer so that LangGraph checkpointers (AsyncPostgresSaver,
MemorySaver, etc.) can serialize and deserialize graph state without warnings
or errors.
"""

from __future__ import annotations

import inspect
from pydantic import BaseModel

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

import core.models as core_models

# Extract all BaseModel classes defined in core.models
ALLOWED_MSGPACK_MODELS: list[type] = [
    obj
    for _, obj in inspect.getmembers(core_models, inspect.isclass)
    if issubclass(obj, BaseModel) and obj.__module__ == core_models.__name__
]


def get_checkpoint_serde() -> JsonPlusSerializer:
    """Return a JsonPlusSerializer configured with core models allowlisted for msgpack."""
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_MSGPACK_MODELS)


def configure_default_serde() -> None:
    """Set BaseCheckpointSaver.serde to allow core.models across all checkpointer instances."""
    BaseCheckpointSaver.serde = get_checkpoint_serde()

