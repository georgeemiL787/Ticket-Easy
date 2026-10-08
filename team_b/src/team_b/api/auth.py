"""Who may call what. SAFETY-CRITICAL (access to customer data). OWNER: Track A.

The rule is deny by default: every request needs a signed-in person unless its path is on the PUBLIC list (the customer
chat, the static pages, health, the login itself). A new route is therefore protected until someone decides otherwise.
For a protected request the middleware checks, in this order:
  1. a valid session cookie (401 UNAUTHENTICATED);
  2. for a request that changes something (POST...), the CSRF token in X-CSRF-Token, and an Origin header that is this
     site's own when there is one (403 FORBIDDEN);
  3. the role the route needs (ROLES; a route not listed there needs admin) (403 FORBIDDEN);
  4. the business: a `tenant_id` the person may not see is answered as if it did not exist (404 TENANT_NOT_FOUND).
The signed-in person is put in request.state.user; handlers use it as the actor and to limit what they list.
Settings.auth_required=0 switches all of this off (local experiments and old tests only).
"""

import re
import secrets
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from team_b.api.errors import ApiError, error_response, tenant_not_found
from team_b.auth import COOKIE_NAME, AuthService, TooManyAttemptsError
from team_b.container import Container
from team_b.domain.users import User

UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "X-CSRF-Token"
API_KEY_HEADER = "X-Api-Key"

# (methods, path pattern) that need no sign-in. HEAD counts as GET.
PUBLIC: tuple[tuple[frozenset[str], re.Pattern[str]], ...] = tuple(
    (frozenset(methods), re.compile(pattern))
    for methods, pattern in (
        (("GET",), r"^/health$"),
        (("GET",), r"^/(chat|inbox|dashboard|ui)(/.*)?$"),  # the pages and their files; the data behind them is protected
        (("POST",), r"^/v1/conversations/[^/]+/messages$"),  # the customer chat (tenant-scoped, optional API key)
        (("GET",), r"^/v1/conversations/[^/]+/(outbox|events)$"),
        (("GET",), r"^/v1/tenants/[^/]+/welcome$"),
        (("POST",), r"^/v1/auth/(login|logout)$"),
        (("GET",), r"^/v1/auth/me$"),
    )
)
TEST_ADMIN = re.compile(r"^/v1/_test/")  # only exists when TEAM_B_ENABLE_TEST_ADMIN=1 (never in production)

# (methods, path pattern, the least role). Anything protected and not listed needs admin.
ROLES: tuple[tuple[frozenset[str], re.Pattern[str], str], ...] = tuple(
    (frozenset(methods), re.compile(pattern), role)
    for methods, pattern, role in (
        (("GET",), r"^/v1/handoff/cases(/[^/]+)?$", "agent"),
        (("POST",), r"^/v1/handoff/cases/[^/]+/(claim|release|reply|resolve|return-to-agent)$", "agent"),
        (("POST",), r"^/v1/handoff/cases/[^/]+/(decision|assign)$", "manager"),  # money, and giving work to others
        (("GET",), r"^/v1/traces/[^/]+$", "agent"),
        (("GET",), r"^/v1/conversations/[^/]+(/traces)?$", "agent"),
        (("GET",), r"^/v1/capabilities$", "manager"),
        (("GET",), r"^/v1/dashboard/.*$", "manager"),
        (("POST",), r"^/v1/dashboard/alerts/[^/]+/ack$", "manager"),
    )
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class Me(BaseModel):
    auth_required: bool
    user: User | None = None
    csrf_token: str | None = None  # send it back in the X-CSRF-Token header on every request that changes something


def unauthenticated(message: str = "sign in first") -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", message)


def is_public(method: str, path: str) -> bool:
    method = "GET" if method == "HEAD" else method
    return any(method in methods and pattern.match(path) for methods, pattern in PUBLIC)


def least_role(method: str, path: str) -> str:
    method = "GET" if method == "HEAD" else method
    return next((role for methods, pattern, role in ROLES if method in methods and pattern.match(path)), "admin")


def auth_of(request: Request) -> AuthService:
    auth: AuthService | None = request.app.state.container.auth
    if auth is None:
        raise ApiError(503, "UPSTREAM_UNAVAILABLE", "sign-in is not available")
    return auth


def current_user(request: Request) -> User | None:
    """The signed-in person (None when nobody is, or when sign-in is switched off)."""
    user: User | None = getattr(request.state, "user", None)
    return user


def acting_name(request: Request, supplied: str | None) -> str:
    """Who is acting: the signed-in person, whatever the request says. Without sign-in, the name that was sent."""
    user = current_user(request)
    if user is not None:
        return user.display_name
    if supplied:
        return supplied
    raise ApiError(422, "INVALID_REQUEST", "agent is required")


def visible_tenants(request: Request, all_ids: tuple[str, ...]) -> tuple[str, ...]:
    """The businesses this request may look at."""
    user = current_user(request)
    return all_ids if user is None else tuple(t for t in all_ids if user.can_see(t))


def check_chat_key(request: Request, tenant_id: str) -> None:
    """The customer chat of a business that has an API key needs it in X-Api-Key."""
    keys = parse_chat_keys(request.app.state.container.settings.chat_api_keys)
    wanted = keys.get(tenant_id)
    if wanted is not None and not secrets.compare_digest(request.headers.get(API_KEY_HEADER, ""), wanted):
        raise ApiError(401, "UNAUTHENTICATED", "this chat needs an API key (X-Api-Key)")


def parse_chat_keys(raw: str) -> dict[str, str]:
    pairs = (item.partition("=") for item in raw.split(",") if item.strip())
    return {t.strip(): k.strip() for t, _, k in pairs if t.strip() and k.strip()}


def _set_cookie(response: Response, value: str, auth: AuthService, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME, value, max_age=auth.max_age_s, httponly=True, samesite="lax", secure=secure, path="/"
    )


@router.post("/login", response_model=Me)
async def login(body: LoginIn, request: Request, response: Response) -> Me:
    built: Container = request.app.state.container
    auth = auth_of(request)
    try:
        user = await auth.authenticate(body.email, body.password)
    except TooManyAttemptsError as exc:
        raise ApiError(
            429, "RATE_LIMITED", "too many attempts, wait a few minutes", {"Retry-After": str(exc.retry_after_s)}
        ) from None
    if user is None:  # one answer for an unknown email, a wrong password and a switched-off account
        raise ApiError(401, "INVALID_CREDENTIALS", "email or password is wrong")
    cookie, session = auth.issue(user)
    _set_cookie(response, cookie, auth, built.settings.cookie_secure)
    return Me(auth_required=True, user=user, csrf_token=session.csrf)


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"signed_out": True}


@router.get("/me", response_model=Me)
async def me(request: Request) -> Me:
    built: Container = request.app.state.container
    if not built.settings.auth_required:
        return Me(auth_required=False)
    found = await auth_of(request).user_for(request.cookies.get(COOKIE_NAME))
    if found is None:
        raise unauthenticated()
    return Me(auth_required=True, user=found[0], csrf_token=found[1].csrf)


def _refuse(request: Request, error: ApiError) -> JSONResponse:
    return error_response(request, error.status_code, error.code, error.message, error.headers)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return origin is None  # a missing Origin (scripts, same-site navigation) is fine; the literal "null" is not
    return urlparse(origin).netloc == request.headers.get("host", "")


async def guard(request: Request, call_next: Any) -> Any:
    """The middleware: see the module docstring for the order of the checks."""
    built: Container | None = getattr(request.app.state, "container", None)
    request.state.user = None
    if built is None or not built.settings.auth_required:
        return await call_next(request)
    path, method = request.url.path, request.method
    if is_public(method, path) or (built.settings.enable_test_admin and TEST_ADMIN.match(path)):
        return await call_next(request)

    found = await auth_of(request).user_for(request.cookies.get(COOKIE_NAME))
    if found is None:
        return _refuse(request, unauthenticated())
    user, session = found
    if method in UNSAFE:
        sent = request.headers.get(CSRF_HEADER, "")
        if not secrets.compare_digest(sent, session.csrf) or not _same_origin(request):
            return _refuse(request, ApiError(403, "FORBIDDEN", "the request did not come from the signed-in page"))
    needed = least_role(method, path)
    if not user.at_least(needed):
        return _refuse(request, ApiError(403, "FORBIDDEN", f"this needs the role {needed}"))
    tenant_id = request.query_params.get("tenant_id")
    if tenant_id is not None and not user.can_see(tenant_id):
        return _refuse(request, tenant_not_found(tenant_id))  # as if it did not exist
    request.state.user = user
    return await call_next(request)


def install(app: FastAPI) -> None:
    app.include_router(router)
    app.middleware("http")(guard)
