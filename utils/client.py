import anthropic

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client
