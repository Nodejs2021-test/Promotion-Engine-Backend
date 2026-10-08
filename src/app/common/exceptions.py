"""Application-wide exception handlers.

Every error response keeps FastAPI's ``detail`` field (backward compatible) and adds a machine-readable envelope
for integrations such as NetSuite:

    {"detail": ..., "error": {"code": "NOT_FOUND", "message": "...", "requestId": "..."}}
"""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError, PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("promotion_engine.errors")

REQUEST_ID_HEADER = "X-Request-ID"

STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "INVALID_REQUEST",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex[:16]


def _message(detail) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(str(x) for x in d.get('loc', []) if x != 'body')}: {d.get('msg')}"
            for d in detail
            if isinstance(d, dict)
        )
    return "Request failed"


def error_response(request: Request, status: int, detail, code: str | None = None, headers: dict | None = None):
    if code is None and isinstance(detail, dict) and isinstance(detail.get("code"), str):
        code = detail["code"]
    rid = request_id(request)
    body = {
        "detail": detail,
        "error": {"code": code or STATUS_CODES.get(status, "ERROR"), "message": _message(detail), "requestId": rid},
    }
    return JSONResponse(status_code=status, content=body, headers={**(headers or {}), REQUEST_ID_HEADER: rid})


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(request, exc.status_code, exc.detail, headers=getattr(exc, "headers", None))


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{k: v for k, v in e.items() if k in ("loc", "msg", "type")} for e in exc.errors()]
    return error_response(request, 422, errors, "INVALID_REQUEST")


async def duplicate_key_handler(request: Request, exc: DuplicateKeyError) -> JSONResponse:
    return error_response(request, 409, "A record with the same identifier already exists", "CONFLICT")


async def database_error_handler(request: Request, exc: PyMongoError) -> JSONResponse:
    log.exception("Database error")
    return error_response(request, 503, "Database unavailable, please try again", "SERVICE_UNAVAILABLE")


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return error_response(request, 500, "Unexpected server error; it has been logged", "INTERNAL_ERROR")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(DuplicateKeyError, duplicate_key_handler)
    app.add_exception_handler(PyMongoError, database_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
