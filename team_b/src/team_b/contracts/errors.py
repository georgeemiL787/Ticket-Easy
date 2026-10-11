"""The one exception every plug adapter raises when an upstream service fails."""


class UpstreamError(Exception):
    """An outside service (policy, rules, safety, shop, AI model) failed or answered nonsense."""

    def __init__(self, service: str, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(f"{service}: {code}: {message}")
        self.service = service
        self.code = code
        self.message = message
        self.retryable = retryable


class InvalidLLMOutput(ValueError):
    """The AI model answered, but not with a JSON object. `raw` is what it said (for one repair attempt)."""

    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw
