"""Role lookups against the ``roles`` collection."""

from app.common.utils import utcnow
from app.core.database import get_db
from app.core.permissions import ADMIN_ROLE, INITIAL_ROLES, PERMISSIONS


async def role_permissions(role: str) -> list[str]:
    doc = await get_db().roles.find_one({"role": role}, {"permissions": 1})
    return sorted(set(doc.get("permissions", [])) & set(PERMISSIONS)) if doc else []


async def ensure_roles() -> None:
    """Create the standard roles that are missing (edited roles are left as they are) and keep every permission on
    the Administrator role."""
    db = get_db()
    for role in INITIAL_ROLES:
        await db.roles.update_one(
            {"role": role["role"]},
            {"$setOnInsert": {**role, "system": True, "created_at": utcnow(), "created_by": "system"}},
            upsert=True,
        )
    await db.roles.update_one({"role": ADMIN_ROLE}, {"$set": {"permissions": sorted(PERMISSIONS)}})
