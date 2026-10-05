"""The one exception every plug adapter raises when an upstream service fails."""


class UpstreamError(Exception):
    """An outside service (policy, rules, safety, shop, AI model) failed or answered nonsense."""

    def __init__(self, service: str, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(f"{service}: {code}: {message}")
        self.service = service
        self.code = code
        self.message = message
        self.retryable = retryable
