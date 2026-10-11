"""Signing in: password hashes, the signed session cookie, and who may do what.

OWNER: Track A. SAFETY-CRITICAL (access to customer data).

  - Passwords are checked against argon2 hashes. An unknown email costs the same time as a wrong password, and both give
    the same answer, so the login cannot be used to find out who has an account.
  - The session is a cookie holding {user id, expiry, csrf token} signed with HMAC-SHA256 (the secret key). It is
    HttpOnly (scripts cannot read it), SameSite=Lax, and Secure when served over https. Every request re-reads the user,
    so a person switched off, or whose role or businesses changed, is affected at once.
  - A state-changing request must also carry the csrf token in the X-CSRF-Token header (checked in api/auth.py).
  - Too many wrong passwords for one email in a short time are refused for a while (Retry-After).
Time here is the real clock (a session must really expire), not the brain's injected one.
"""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from team_b.domain.users import Role, User
from team_b.ports import UserStore

COOKIE_NAME = "tb_session"
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200
MAX_FAILURES, FAILURE_WINDOW_S = 5, 15 * 60

_HASHER = PasswordHasher()  # argon2id with the library's current defaults
_DUMMY_HASH = _HASHER.hash("not a real password " + secrets.token_hex(8))


class WeakPasswordError(ValueError):
    """The password is too short or too long."""


class TooManyAttemptsError(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__("too many sign-in attempts")
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class Session:
    user_id: str
    expires_at: float
    csrf: str


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"the password needs at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise WeakPasswordError(f"the password may have at most {MAX_PASSWORD_LENGTH} characters")
    return _HASHER.hash(password)


def password_matches(password: str, password_hash: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password[:MAX_PASSWORD_LENGTH])
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class AuthService:
    def __init__(
        self,
        users: UserStore,
        secret: bytes,
        *,
        session_hours: float = 12.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._users, self._secret, self._ttl, self._now = users, secret, session_hours * 3600, clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    # ---- users ----

    async def create_user(
        self, email: str, display_name: str, role: Role, tenants: tuple[str, ...], password: str
    ) -> User:
        """Add a person. Raises WeakPasswordError, or AlreadyExistsError for a taken email or display name."""
        user = User(
            user_id=uuid.uuid4().hex,
            email=email.strip(),
            display_name=" ".join(display_name.split()),
            role=role,
            tenants=tuple(dict.fromkeys(tenants)),
            created_at=datetime.now(UTC),
        )
        await self._users.create(user, hash_password(password))
        return user

    async def authenticate(self, email: str, password: str) -> User | None:
        """The user if the password is right and the account is active, else None (one answer for every reason)."""
        key = email.strip().lower()
        self._check_throttle(key)
        found = await self._users.get_with_hash(key)
        stored = found[1] if found is not None else _DUMMY_HASH  # an unknown email still costs one hash check
        ok = password_matches(password, stored)
        if found is None or not ok or not found[0].active:
            self._record_failure(key)
            return None
        self._failures.pop(key, None)
        return found[0]

    def _check_throttle(self, key: str) -> None:
        window = self._failures[key]
        now = self._now()
        while window and window[0] <= now - FAILURE_WINDOW_S:
            window.popleft()
        if len(window) >= MAX_FAILURES:
            raise TooManyAttemptsError(int(window[0] + FAILURE_WINDOW_S - now) + 1)

    def _record_failure(self, key: str) -> None:
        self._failures[key].append(self._now())

    # ---- the session cookie ----

    def issue(self, user: User) -> tuple[str, Session]:
        """A signed cookie value and the session it holds."""
        session = Session(user.user_id, self._now() + self._ttl, secrets.token_urlsafe(24))
        body = _b64(json.dumps({"u": session.user_id, "x": session.expires_at, "c": session.csrf}).encode())
        return f"{body}.{self._sign(body)}", session

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())

    def read(self, cookie: str | None) -> Session | None:
        """The session in a cookie, or None when it is missing, forged or expired."""
        if not cookie or cookie.count(".") != 1:
            return None
        body, signature = cookie.split(".")
        if not hmac.compare_digest(signature, self._sign(body)):
            return None
        try:
            data = json.loads(_unb64(body))
            session = Session(str(data["u"]), float(data["x"]), str(data["c"]))
        except (ValueError, KeyError, TypeError, binascii.Error):
            return None
        return session if session.expires_at > self._now() else None

    async def user_for(self, cookie: str | None) -> tuple[User, Session] | None:
        """The signed-in person for a cookie, read fresh from the store (so a switched-off account stops at once)."""
        session = self.read(cookie)
        if session is None:
            return None
        user = await self._users.get(session.user_id)
        return (user, session) if user is not None and user.active else None

    @property
    def max_age_s(self) -> int:
        return int(self._ttl)
