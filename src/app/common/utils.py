"""Shared helpers: dates, money, search and Mongo document serialisation."""

import re
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from bson import ObjectId

from app.core import app_settings

CENT = Decimal("0.01")
# Fields that hold business dates (stored as UTC-midnight datetimes, returned as YYYY-MM-DD).
DATE_FIELDS = {"start_date", "end_date", "order_date"}


def utcnow() -> datetime:
    return datetime.now(UTC)


def today() -> date:
    return datetime.now(ZoneInfo(app_settings.timezone_name())).date()


def to_dt(d: date | None) -> datetime | None:
    """Business date -> naive UTC-midnight datetime for storage."""
    if d is None:
        return None
    if isinstance(d, datetime):
        d = d.date()
    return datetime(d.year, d.month, d.day)


def to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def money(v: Any) -> Decimal:
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


def serialize(obj: Any, key: str | None = None) -> Any:
    """Convert a Mongo document into JSON-friendly data (drops ``_id``)."""
    if isinstance(obj, dict):
        return {k: serialize(v, k) for k, v in obj.items() if k != "_id"}
    if isinstance(obj, (list, tuple)):
        return [serialize(v, key) for v in obj]
    if isinstance(obj, datetime):
        if key in DATE_FIELDS:
            return obj.date().isoformat()
        if obj.tzinfo is None:
            return obj.isoformat() + "Z"
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, ObjectId):
        return str(obj)
    return obj


def search_regex(q: str) -> dict:
    return {"$regex": re.escape(q.strip()), "$options": "i"}
