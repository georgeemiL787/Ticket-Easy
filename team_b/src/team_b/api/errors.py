"""One error format for the whole API: {"schema_version": "1.0", "error": {"code", "message", "request_id"}}."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from team_b.contracts.errors import UpstreamError
from team_b.domain.tenant import UnknownTenantError
from team_b.observability import get_logger
from team_b.ports import SessionConflictError

SCHEMA_VERSION = "1.0"
log = get_logger(__name__)


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    schema_version: str = SCHEMA_VERSION
    error: ErrorBody


class ApiError(Exception):
    """Raise this from a route to answer with the standard error format."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def invalid_request(message: str) -> ApiError:
    return ApiError(422, "INVALID_REQUEST", message)


def not_found(message: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", message)


def tenant_not_found(tenant_id: str) -> ApiError:
    return ApiError(404, "TENANT_NOT_FOUND", f"unknown tenant: {tenant_id}")


def invalid_state(message: str) -> ApiError:
    return ApiError(409, "INVALID_STATE", message)


def upstream_unavailable(message: str) -> ApiError:
    return ApiError(503, "UPSTREAM_UNAVAILABLE", message)


def request_id_of(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    envelope = ErrorEnvelope(error=ErrorBody(code=code, message=message, request_id=request_id_of(request)))
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def _describe_validation_errors(exc: RequestValidationError) -> str:
    """Field and reason only. The offending value is never echoed back (it may be personal data)."""
    parts = []
    for error in exc.errors():
        where = ".".join(str(p) for p in error["loc"])
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts) or "invalid request"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, 422, "INVALID_REQUEST", _describe_validation_errors(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return error_response(request, 404, "NOT_FOUND", "not found")
        code = "INVALID_REQUEST" if exc.status_code < 500 else "INTERNAL_ERROR"
        return error_response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(UnknownTenantError)
    async def _unknown_tenant(request: Request, exc: UnknownTenantError) -> JSONResponse:
        tenant = str(exc.args[0]) if exc.args else "unknown"
        return error_response(request, 404, "TENANT_NOT_FOUND", f"unknown tenant: {tenant}")

    @app.exception_handler(SessionConflictError)
    async def _conflict(request: Request, exc: SessionConflictError) -> JSONResponse:
        return error_response(request, 409, "INVALID_STATE", "the conversation changed at the same time, try again")

    @app.exception_handler(UpstreamError)
    async def _upstream(request: Request, exc: UpstreamError) -> JSONResponse:
        log.warning("upstream_unavailable", service=exc.service, code=exc.code, retryable=exc.retryable)
        return error_response(request, 503, "UPSTREAM_UNAVAILABLE", f"{exc.service} is unavailable")

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error_type=type(exc).__name__, exc_info=exc)
        return error_response(request, 500, "INTERNAL_ERROR", "unexpected error")


def error_responses_doc() -> dict[int | str, dict[str, Any]]:
    """OpenAPI description of the error format, for routes that want to document it."""
    return {code: {"model": ErrorEnvelope} for code in (404, 409, 422, 503)}
