"""Liveness / readiness probe (unversioned, no authentication)."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.common.utils import today
from app.core import app_settings
from app.core.database import check_connection

router = APIRouter(tags=["System"])


@router.get("/api/health")
async def health():
    try:
        mongo = await check_connection()
    except RuntimeError as exc:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(exc)})
    return {
        "status": "ok",
        "mongodb": mongo,
        "initialized": app_settings.current() is not None,
        "business_date": today().isoformat(),
        "timezone": app_settings.timezone_name(),
    }
