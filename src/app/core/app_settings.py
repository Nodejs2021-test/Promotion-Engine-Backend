"""Business settings stored in MongoDB (collection ``app_settings``, single document ``_id: "app"``).

They are created by the first-run setup and edited by administrators. A short in-memory cache avoids a
database round trip on every request; it is refreshed at most every ``TTL_SECONDS`` and immediately after
an update made by this process.
"""

import time

from app.core.database import get_db

SETTINGS_ID = "app"
TTL_SECONDS = 30.0

_cache: dict = {"data": None, "loaded_at": 0.0}


def current() -> dict | None:
    """Cached settings, or None when the application has not been set up yet."""
    return _cache["data"]


def timezone_name() -> str:
    data = current()
    return data["timezone"] if data else "UTC"


def currency() -> str | None:
    data = current()
    return data["currency"] if data else None


CONTROLLED_ATTRIBUTES = ("channel", "banner", "marketing_flag")


def controlled_values() -> dict[str, list[str]]:
    """Controlled Channel / Banner / Marketing Flag values (empty list = not controlled)."""
    data = current() or {}
    stored = data.get("controlled_values") or {}
    return {k: list(stored.get(k) or []) for k in CONTROLLED_ATTRIBUTES}


async def refresh(force: bool = False) -> dict | None:
    if force or time.monotonic() - _cache["loaded_at"] > TTL_SECONDS:
        doc = await get_db().app_settings.find_one({"_id": SETTINGS_ID})
        _cache["data"] = {k: v for k, v in doc.items() if k != "_id"} if doc else None
        _cache["loaded_at"] = time.monotonic()
    return _cache["data"]


def clear_cache() -> None:
    _cache["data"] = None
    _cache["loaded_at"] = 0.0
