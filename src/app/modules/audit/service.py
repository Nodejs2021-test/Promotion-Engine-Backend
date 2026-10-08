"""Audit trail for configuration changes."""

from typing import Any

from app.common.utils import utcnow
from app.core.database import get_db


def _strip(doc: Any) -> Any:
    if isinstance(doc, dict):
        return {k: _strip(v) for k, v in doc.items() if k not in ("_id", "password_hash")}
    if isinstance(doc, list):
        return [_strip(v) for v in doc]
    return doc


def changed_fields(before: dict | None, after: dict | None) -> list[str]:
    before, after = before or {}, after or {}
    ignore = {"_id", "updated_at", "updated_by"}
    keys = (set(before) | set(after)) - ignore
    return sorted(k for k in keys if before.get(k) != after.get(k))


async def write_audit(
    entity_type: str,
    entity_id: str,
    action: str,
    user: str,
    before: dict | None = None,
    after: dict | None = None,
    details: str | None = None,
) -> None:
    await get_db().audit_logs.insert_one(
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "user": user,
            "timestamp": utcnow(),
            "details": details,
            "changed_fields": changed_fields(before, after) if before is not None and after is not None else [],
            "before": _strip(before),
            "after": _strip(after),
        }
    )
