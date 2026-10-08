"""Promotion Engine - FastAPI application factory."""

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pymongo.errors import PyMongoError

from app.api import health
from app.api.v1.router import router as api_v1_router
from app.common.exceptions import (
    REQUEST_ID_HEADER,
    error_response,
    register_exception_handlers,
)
from app.core import app_settings
from app.core.collections import init_database
from app.core.config import settings
from app.core.database import close_client
from app.modules.roles.service import ensure_roles

DESCRIPTION = (
    "Receives sales orders from NetSuite (or any system sending the normalised order), maps them with an editable "
    "field mapping, prices them with the approved campaigns and stores them. All data (campaigns, sales orders, users, "
    "roles, settings and audit history) is stored in MongoDB. On a new database, register the first account (`POST /api/v1/auth/register`); it becomes the "
    "administrator."
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fails fast with a clear message when MongoDB is unreachable; creates collections, validators, indexes, roles.
    await init_database()
    await ensure_roles()
    await app_settings.refresh(force=True)
    yield
    await close_client()


def create_app() -> FastAPI:
    app = FastAPI(title="Promotion Engine", version="3.0.0", description=DESCRIPTION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Readable by the browser: the request id and the file name of attachment downloads.
        expose_headers=[REQUEST_ID_HEADER, "Content-Disposition"],
    )
    if settings.trusted_host_list != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)

    @app.middleware("http")
    async def load_business_settings(request: Request, call_next):
        # A request id for every call (taken from the caller when it sends one) ties logs, errors and responses.
        request.state.request_id = (request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16])[:64]
        # Keeps the cached business settings (timezone, currency) in sync with MongoDB across workers.
        try:
            await app_settings.refresh()
        except PyMongoError:
            return error_response(request, 503, "Database unavailable", "SERVICE_UNAVAILABLE")
        response = await call_next(request)
        response.headers.setdefault(REQUEST_ID_HEADER, request.state.request_id)
        return response

    register_exception_handlers(app)
    app.include_router(api_v1_router)
    app.include_router(health.router)
    return app


app = create_app()
