"""Roles and permissions (stored in MongoDB, editable by administrators)."""

from fastapi import APIRouter, HTTPException

from app.api.dependencies import CurrentUser, UserAdmin
from app.common.utils import serialize, utcnow
from app.core.database import get_db
from app.core.permissions import ADMIN_REQUIRED, ADMIN_ROLE, PERMISSIONS
from app.modules.audit.service import write_audit
from app.modules.roles.schema import RoleIn, RoleUpdate

router = APIRouter(tags=["Roles & permissions"])


def _check_permissions(perms: list[str]) -> list[str]:
    unknown = sorted(set(perms) - set(PERMISSIONS))
    if unknown:
        raise HTTPException(422, f"Unknown permission(s): {', '.join(unknown)}")
    return sorted(set(perms))


@router.get("/roles")
async def list_roles(_: CurrentUser):
    db = get_db()
    out = []
    async for r in db.roles.find().sort("role", 1):
        row = serialize(r)
        row["user_count"] = await db.users.count_documents({"role": r["role"]})
        out.append(row)
    return out


@router.post("/roles", status_code=201)
async def create_role(body: RoleIn, admin: UserAdmin):
    db = get_db()
    if await db.roles.find_one({"role": body.role}):
        raise HTTPException(409, f"Role {body.role} already exists")
    now = utcnow()
    doc = {
        "role": body.role,
        "label": body.label,
        "description": body.description,
        "permissions": _check_permissions(body.permissions),
        "system": False,
        "created_at": now,
        "created_by": admin["username"],
        "updated_at": now,
        "updated_by": admin["username"],
    }
    await db.roles.insert_one(doc)
    await write_audit("ROLE", body.role, "CREATE", admin["username"], after=doc)
    return serialize(doc)


@router.put("/roles/{role}")
async def update_role(role: str, body: RoleUpdate, admin: UserAdmin):
    db = get_db()
    before = await db.roles.find_one({"role": role})
    if not before:
        raise HTTPException(404, f"Role {role} not found")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("permissions") is not None:
        changes["permissions"] = _check_permissions(changes["permissions"])
        if role == ADMIN_ROLE and not ADMIN_REQUIRED <= set(changes["permissions"]):
            raise HTTPException(422, f"The {ADMIN_ROLE} role must keep: {', '.join(sorted(ADMIN_REQUIRED))}")
    changes.update(updated_at=utcnow(), updated_by=admin["username"])
    await db.roles.update_one({"role": role}, {"$set": changes})
    after = await db.roles.find_one({"role": role})
    await write_audit("ROLE", role, "UPDATE", admin["username"], before, after)
    return serialize(after)


@router.delete("/roles/{role}", status_code=204)
async def delete_role(role: str, admin: UserAdmin):
    db = get_db()
    doc = await db.roles.find_one({"role": role})
    if not doc:
        raise HTTPException(404, f"Role {role} not found")
    if doc.get("system"):
        raise HTTPException(409, "Built-in roles cannot be deleted; change their permissions instead")
    if await db.users.count_documents({"role": role}):
        raise HTTPException(409, "Role is still assigned to users")
    await db.roles.delete_one({"role": role})
    await write_audit("ROLE", role, "DELETE", admin["username"], before=doc)
