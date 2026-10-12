import threading
import anthropic

DEFAULT_MODEL = "claude-sonnet-5"

# Explicit rather than relying on the SDK's own default -- code_generator.py
# and test_generator.py stream up to max_tokens=64000, which legitimately
# takes a while, but a hung connection must not block a run forever.
DEFAULT_TIMEOUT_SECONDS = 600.0

_model = DEFAULT_MODEL
_lock = threading.Lock()
_accumulated_tokens = {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "step_tokens": 0,
}


def set_model(model: str) -> None:
    """Called once by the adapter layer after resolving config (see
    tools/config.py) -- keeps core/ itself unaware of where the model name
    came from."""
    global _model
    _model = model


def get_model() -> str:
    return _model


def record_tokens(input_tokens: int, output_tokens: int) -> None:
    with _lock:
        _accumulated_tokens["input_tokens"] += input_tokens
        _accumulated_tokens["output_tokens"] += output_tokens
        total = input_tokens + output_tokens
        _accumulated_tokens["total_tokens"] += total
        _accumulated_tokens["step_tokens"] += total


def reset_tokens() -> None:
    with _lock:
        _accumulated_tokens["input_tokens"] = 0
        _accumulated_tokens["output_tokens"] = 0
        _accumulated_tokens["total_tokens"] = 0
        _accumulated_tokens["step_tokens"] = 0


def consume_step_tokens() -> int:
    with _lock:
        tok = _accumulated_tokens["step_tokens"]
        _accumulated_tokens["step_tokens"] = 0
        return tok


def get_accumulated_tokens() -> dict:
    with _lock:
        return dict(_accumulated_tokens)


class _StreamContextWrapper:
    def __init__(self, ctx_manager):
        self._ctx_manager = ctx_manager
        self._stream = None

    def __enter__(self):
        self._stream = self._ctx_manager.__enter__()
        original_get_final_message = self._stream.get_final_message

        def tracked_get_final_message():
            msg = original_get_final_message()
            if hasattr(msg, "usage") and msg.usage:
                in_tok = getattr(msg.usage, "input_tokens", 0) or 0
                out_tok = getattr(msg.usage, "output_tokens", 0) or 0
                record_tokens(in_tok, out_tok)
            return msg

        self._stream.get_final_message = tracked_get_final_message
        return self._stream

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._ctx_manager.__exit__(exc_type, exc_val, exc_tb)


class _MessagesProxy:
    def __init__(self, messages):
        self._messages = messages

    def stream(self, *args, **kwargs):
        ctx = self._messages.stream(*args, **kwargs)
        return _StreamContextWrapper(ctx)

    def create(self, *args, **kwargs):
        msg = self._messages.create(*args, **kwargs)
        if hasattr(msg, "usage") and msg.usage:
            in_tok = getattr(msg.usage, "input_tokens", 0) or 0
            out_tok = getattr(msg.usage, "output_tokens", 0) or 0
            record_tokens(in_tok, out_tok)
        return msg

    def __getattr__(self, name):
        return getattr(self._messages, name)


class _ClientProxy:
    def __init__(self, client):
        self._client = client
        self.messages = _MessagesProxy(client.messages)

    def __getattr__(self, name):
        return getattr(self._client, name)


def get_client() -> anthropic.Anthropic:
    raw_client = anthropic.Anthropic(timeout=DEFAULT_TIMEOUT_SECONDS)
    return _ClientProxy(raw_client)

