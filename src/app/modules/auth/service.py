"""Sign-in sessions and first-run initialisation (organisation settings and the first administrator)."""

from zoneinfo import available_timezones

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.common.utils import serialize, utcnow
from app.core import app_settings
from app.core.database import get_db
from app.core.permissions import ADMIN_ROLE
from app.core.security import create_access_token, hash_password
from app.modules.audit.service import write_audit
from app.modules.roles.service import role_permissions


async def session(user: dict) -> dict:
    out = serialize({k: v for k, v in user.items() if k != "password_hash"})
    out["permissions"] = await role_permissions(user["role"])
    role = await get_db().roles.find_one({"role": user["role"]}, {"label": 1})
    out["role_label"] = role["label"] if role else user["role"]
    return {"access_token": create_access_token(user["username"], user["role"]), "token_type": "bearer", "user": out}


async def initialise(
    organization_name: str,
    currency: str,
    timezone: str,
    username: str,
    full_name: str,
    email: str | None,
    password: str,
) -> dict:
    """Store the organisation settings, create the roles and the first administrator."""
    if timezone not in available_timezones():
        raise HTTPException(422, f"Unknown timezone '{timezone}'")
    db = get_db()
    if await app_settings.refresh(force=True):
        raise HTTPException(409, "The application has already been initialised")
    if await db.users.find_one({"username": username}):
        raise HTTPException(409, f"Username {username} is already taken")
    now = utcnow()
    settings_doc = {
        "_id": app_settings.SETTINGS_ID,
        "organization_name": organization_name.strip(),
        "currency": currency.upper(),
        "timezone": timezone,
        "initialized_at": now,
        "initialized_by": username,
        "updated_at": now,
        "updated_by": username,
    }
    try:
        # The fixed _id makes initialisation a one-time operation even under concurrent requests.
        await db.app_settings.insert_one(settings_doc)
    except DuplicateKeyError:
        raise HTTPException(409, "The application has already been initialised") from None

    user = {
        "username": username,
        "full_name": full_name,
        "email": email,
        "role": ADMIN_ROLE,
        "active": True,
        "registration_status": "APPROVED",
        "password_hash": hash_password(password),
        "created_at": now,
        "updated_at": now,
        "created_by": "registration",
    }
    await db.users.insert_one(user)
    await app_settings.refresh(force=True)
    await write_audit("SETTINGS", app_settings.SETTINGS_ID, "SETUP", username, after=settings_doc)
    await write_audit(
        "USER", username, "REGISTER", username, after=user, details="First account registered: Administrator"
    )
    return await session(user)
