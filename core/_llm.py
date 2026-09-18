import anthropic

DEFAULT_MODEL = "claude-sonnet-5"

# Explicit rather than relying on the SDK's own default -- code_generator.py
# and test_generator.py stream up to max_tokens=64000, which legitimately
# takes a while, but a hung connection must not block a run forever.
DEFAULT_TIMEOUT_SECONDS = 600.0

_model = DEFAULT_MODEL


def set_model(model: str) -> None:
    """Called once by the adapter layer after resolving config (see
    tools/config.py) -- keeps core/ itself unaware of where the model name
    came from."""
    global _model
    _model = model


def get_model() -> str:
    return _model


def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(timeout=DEFAULT_TIMEOUT_SECONDS)
