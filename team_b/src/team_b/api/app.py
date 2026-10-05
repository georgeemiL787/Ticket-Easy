"""The HTTP API. Start it with: make run  (port 8010)."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from team_b.api.errors import install_error_handlers
from team_b.config import Settings
from team_b.container import Container, build_container
from team_b.observability import bind_context, clear_context, configure_logging, get_logger

REQUEST_ID_HEADER = "X-Request-ID"
log = get_logger(__name__)


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
        log.info("started", mode=built.settings.mode, tenants=list(built.tenants.tenant_ids()))
        yield

    app = FastAPI(title="Ticket-Easy Team B", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)

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
