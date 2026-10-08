"""User administration, including approval of self-registered accounts."""

from fastapi import APIRouter, HTTPException

from app.api.dependencies import UserAdmin
from app.common.utils import utcnow
from app.core.database import get_db
from app.core.permissions import ADMIN_ROLE
from app.core.security import hash_password
from app.modules.audit.service import write_audit
from app.modules.users.schema import ApproveIn, UserIn, UserUpdate
from app.modules.users.service import user_out

router = APIRouter(tags=["Users"])


async def _require_role(role: str) -> None:
    if not await get_db().roles.find_one({"role": role}, {"_id": 1}):
        raise HTTPException(422, f"Role {role} does not exist")


@router.get("/users")
async def list_users(_: UserAdmin, registration_status: str | None = None):
    query = {"registration_status": registration_status} if registration_status else {}
    return [await user_out(u) async for u in get_db().users.find(query).sort([("created_at", -1)])]


@router.post("/users", status_code=201)
async def create_user(body: UserIn, admin: UserAdmin):
    db = get_db()
    await _require_role(body.role)
    if await db.users.find_one({"username": body.username}):
        raise HTTPException(409, f"User {body.username} already exists")
    now = utcnow()
    doc = {
        **body.model_dump(exclude={"password"}),
        "password_hash": hash_password(body.password),
        "created_at": now,
        "updated_at": now,
        "created_by": admin["username"],
    }
    await db.users.insert_one(doc)
    await write_audit("USER", body.username, "CREATE", admin["username"], after=doc)
    return await user_out(doc)


@router.put("/users/{username}")
async def update_user(username: str, body: UserUpdate, admin: UserAdmin):
    db = get_db()
    before = await db.users.find_one({"username": username})
    if not before:
        raise HTTPException(404, f"User {username} not found")
    changes = body.model_dump(exclude_unset=True, exclude={"password"})
    if changes.get("role"):
        await _require_role(changes["role"])
    if body.password:
        changes["password_hash"] = hash_password(body.password)
    if username == admin["username"] and (
        changes.get("active") is False or changes.get("role", ADMIN_ROLE) != ADMIN_ROLE
    ):
        raise HTTPException(400, "You cannot deactivate yourself or remove your own ADMIN role")
    if changes.get("active") is True and before.get("registration_status") == "PENDING":
        changes["registration_status"] = "APPROVED"
        changes["approved_by"] = admin["username"]
        changes["approved_at"] = utcnow()
    changes["updated_at"] = utcnow()
    await db.users.update_one({"username": username}, {"$set": changes})
    after = await db.users.find_one({"username": username})
    await write_audit("USER", username, "UPDATE", admin["username"], before, after)
    return await user_out(after)


@router.post("/users/{username}/approve")
async def approve_registration(username: str, body: ApproveIn, admin: UserAdmin):
    """Activate a self-registered account and give it a role."""
    db = get_db()
    before = await db.users.find_one({"username": username})
    if not before:
        raise HTTPException(404, f"User {username} not found")
    if before.get("registration_status") != "PENDING":
        raise HTTPException(409, f"User {username} is not awaiting approval")
    await _require_role(body.role)
    now = utcnow()
    await db.users.update_one(
        {"username": username},
        {
            "$set": {
                "active": True,
                "role": body.role,
                "registration_status": "APPROVED",
                "approved_by": admin["username"],
                "approved_at": now,
                "updated_at": now,
            }
        },
    )
    after = await db.users.find_one({"username": username})
    await write_audit("USER", username, "APPROVE", admin["username"], before, after)
    return await user_out(after)


@router.delete("/users/{username}/registration", status_code=204)
async def reject_registration(username: str, admin: UserAdmin):
    """Reject (delete) a self-registered account that is still awaiting approval."""
    db = get_db()
    before = await db.users.find_one({"username": username})
    if not before or before.get("registration_status") != "PENDING":
        raise HTTPException(409, f"User {username} is not awaiting approval")
    await db.users.delete_one({"username": username})
    await write_audit("USER", username, "REJECT_REGISTRATION", admin["username"], before=before)
