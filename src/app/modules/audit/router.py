"""Configuration audit trail."""

from datetime import date

from fastapi import APIRouter

from app.api.dependencies import Viewer
from app.common.pagination import Page, PageSize, paginate
from app.common.utils import search_regex, to_dt
from app.core.database import get_db

router = APIRouter(tags=["Audit"])


@router.get("/audit")
async def list_audit(
    _: Viewer,
    entity_type: str | None = None,
    entity_id: str | None = None,
    user: str | None = None,
    action: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: Page = 1,
    page_size: PageSize = 50,
):
    query: dict = {}
    if entity_type:
        query["entity_type"] = entity_type
    if entity_id:
        query["entity_id"] = search_regex(entity_id)
    if user:
        query["user"] = user
    if action:
        query["action"] = action
    if date_from or date_to:
        rng = {}
        if date_from:
            rng["$gte"] = to_dt(date_from)
        if date_to:
            rng["$lt"] = to_dt(date.fromordinal(date_to.toordinal() + 1))
        query["timestamp"] = rng
    return await paginate(get_db().audit_logs, query, [("timestamp", -1)], page, page_size)


@router.get("/audit/facets")
async def audit_facets(_: Viewer):
    db = get_db()
    return {
        "entity_types": sorted(await db.audit_logs.distinct("entity_type")),
        "actions": sorted(await db.audit_logs.distinct("action")),
        "users": sorted(await db.audit_logs.distinct("user")),
    }
