"""The HTTP API. Start it with: make run  (port 8010)."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from team_b.api import chat, dashboard, inbox, traces
from team_b.api.errors import install_error_handlers
from team_b.api.ratelimit import RateLimiter
from team_b.config import PROJECT_ROOT, Settings
from team_b.container import Container, build_container
from team_b.observability import bind_context, clear_context, configure_logging, get_logger
from team_b.retention import retention_loop

REQUEST_ID_HEADER = "X-Request-ID"
log = get_logger(__name__)


CHAT_DIR = PROJECT_ROOT / "web" / "chat"
INBOX_DIR = PROJECT_ROOT / "web" / "inbox"


def mount_chat_page(app: FastAPI) -> None:
    """The browser chat: /chat?tenant_id=shop_001 (plain HTML and JavaScript, no build step)."""
    if not (CHAT_DIR / "index.html").is_file():
        return

    @app.get("/chat", include_in_schema=False)
    async def chat_page() -> FileResponse:
        return FileResponse(CHAT_DIR / "index.html", media_type="text/html")

    app.mount("/chat/static", StaticFiles(directory=CHAT_DIR), name="chat-static")


def mount_inbox_page(app: FastAPI) -> None:
    """The support inbox: /inbox (plain HTML and JavaScript, no build step)."""
    if not (INBOX_DIR / "index.html").is_file():
        return

    @app.get("/inbox", include_in_schema=False)
    async def inbox_page() -> FileResponse:
        return FileResponse(INBOX_DIR / "index.html", media_type="text/html")

    app.mount("/inbox/static", StaticFiles(directory=INBOX_DIR), name="inbox-static")


def create_app(*, settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    """Build the app. The container is built at startup from settings (or the environment) unless one is given."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        built = container
        if built is None:
            resolved = settings or Settings.from_env()
            configure_logging(json_logs=resolved.log_json)
            built = build_container(resolved)
        app.state.container = built
        app.state.events = built.events
        app.state.rate_limiter = RateLimiter(built.settings.rate_limit_per_minute)
        log.info("started", mode=built.settings.mode, tenants=list(built.tenants.tenant_ids()))
        retention = asyncio.create_task(retention_loop(built))
        try:
            yield
        finally:
            retention.cancel()
            await asyncio.gather(retention, return_exceptions=True)

    app = FastAPI(title="Ticket-Easy Team B", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(chat.router)
    app.include_router(traces.router)
    app.include_router(inbox.router)
    app.include_router(dashboard.router)
    mount_chat_page(app)
    mount_inbox_page(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        clear_context()
        bind_context(request_id=request_id)
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.get("/health")
    async def health(request: Request) -> dict[str, object]:
        built: Container = request.app.state.container
        return {"status": "ok", "mode": built.settings.mode, "tenants": list(built.tenants.tenant_ids())}

    return app


app = create_app()
