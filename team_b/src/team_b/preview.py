"""Preview tokens: a signed, short-lived pass that lets staff chat with a business that is not live yet.

A business that is still being set up answers customers with TENANT_NOT_LIVE. The onboarding wizard asks for a token
for one business and opens the chat page with it (?preview=<token>); the page sends it in X-Preview-Token. The token
names the business and an expiry and is signed with the service secret, so it cannot be made for another business
or extended.
"""

import hashlib
import hmac
import time

TTL_S = 3600


class PreviewTokens:
    def __init__(self, secret: bytes, ttl_s: int = TTL_S) -> None:
        self._secret = secret
        self._ttl = ttl_s

    def _sign(self, tenant_id: str, expires: int) -> str:
        return hmac.new(self._secret, f"preview:{tenant_id}:{expires}".encode(), hashlib.sha256).hexdigest()[:32]

    def issue(self, tenant_id: str, now: float | None = None) -> str:
        expires = int((time.time() if now is None else now) + self._ttl)
        return f"{expires}.{self._sign(tenant_id, expires)}"

    def valid(self, token: str, tenant_id: str, now: float | None = None) -> bool:
        expires_text, _, signature = token.partition(".")
        if not expires_text.isdigit() or not signature:
            return False
        expires = int(expires_text)
        if expires < (time.time() if now is None else now):
            return False
        return hmac.compare_digest(signature, self._sign(tenant_id, expires))
