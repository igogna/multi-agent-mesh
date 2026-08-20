import anthropic

DEFAULT_MODEL = "claude-sonnet-5"


def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()
