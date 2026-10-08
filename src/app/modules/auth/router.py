"""Sign in, current user, password change, registration and first-run setup.

No default users or data are shipped with the application:

* The **first** account registered on an empty database becomes the Administrator. That registration also
  stores the organisation settings (name, currency, timezone) and creates the roles in MongoDB.
* Accounts registered **afterwards** are created as ``PENDING`` (inactive, read-only role) and can sign in
  once an administrator approves them under Administration -> Users.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from app.api.dependencies import CurrentUser
from app.common.utils import utcnow
from app.core import app_settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.modules.audit.service import write_audit
from app.modules.auth.schema import PasswordChange, RegisterIn, SetupIn
from app.modules.auth.service import initialise
from app.modules.settings import service as uploads
from app.modules.users.service import user_out

router = APIRouter(tags=["Auth"])


# Role given to self-registered accounts until an administrator changes it.
REGISTRATION_ROLE = "READ_ONLY"


@router.post("/auth/login")
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    user = await get_db().users.find_one({"username": form.username})
    if not user or not verify_password(form.password, user.get("password_hash", "")):
        raise HTTPException(401, "Invalid username or password")
    if not user.get("active", True):
        if user.get("registration_status") == "PENDING":
            raise HTTPException(403, "Your account is awaiting approval by an administrator")
        raise HTTPException(403, "Your account has been deactivated. Contact an administrator.")
    await get_db().users.update_one({"_id": user["_id"]}, {"$set": {"last_login_at": utcnow()}})
    return {
        "access_token": create_access_token(user["username"], user["role"]),
        "token_type": "bearer",
        "user": await user_out(user),
    }


@router.get("/auth/me")
async def me(user: CurrentUser):
    return await user_out(user)


@router.post("/auth/change-password", status_code=204)
async def change_password(body: PasswordChange, user: CurrentUser):
    full = await get_db().users.find_one({"username": user["username"]})
    if not verify_password(body.current_password, full["password_hash"]):
        raise HTTPException(400, "Current password is incorrect")
    await get_db().users.update_one(
        {"username": user["username"]},
        {"$set": {"password_hash": hash_password(body.new_password), "updated_at": utcnow()}},
    )
    await write_audit("USER", user["username"], "CHANGE_PASSWORD", user["username"])


@router.get("/setup/status")
async def setup_status():
    settings_doc = await app_settings.refresh(force=True)
    logo = await uploads.current_logo()
    return {
        "initialized": settings_doc is not None,
        "organization_name": settings_doc.get("organization_name") if settings_doc else None,
        # Changes whenever a new logo is uploaded, so the UI can refresh its cached image.
        "logo_version": logo["version"] if logo else None,
        "logo_uploaded": bool(logo and logo["uploaded"]),
    }


@router.post("/auth/register", status_code=201, tags=["Auth & users"])
async def register(body: RegisterIn):
    """Create an account. The first account becomes the Administrator; later accounts await approval."""
    if not await app_settings.refresh(force=True):
        missing = [f for f in ("organization_name", "currency", "timezone") if not getattr(body, f)]
        if missing:
            raise HTTPException(422, f"The first account also sets up the organisation: {', '.join(missing)} required")
        session = await initialise(
            body.organization_name,
            body.currency,
            body.timezone,
            body.username,
            body.full_name,
            body.email,
            body.password,
        )
        return {"status": "ACTIVE", "first_user": True, **session}

    db = get_db()
    if await db.users.find_one({"username": body.username}):
        raise HTTPException(409, f"Username {body.username} is already taken")
    now = utcnow()
    user = {
        "username": body.username,
        "full_name": body.full_name,
        "email": body.email,
        "role": REGISTRATION_ROLE,
        "active": False,
        "registration_status": "PENDING",
        "password_hash": hash_password(body.password),
        "created_at": now,
        "updated_at": now,
        "created_by": "registration",
    }
    await db.users.insert_one(user)
    await write_audit(
        "USER",
        body.username,
        "REGISTER",
        body.username,
        after=user,
        details="Self-registered; awaiting administrator approval",
    )
    return {
        "status": "PENDING",
        "first_user": False,
        "message": "Account created. An administrator must approve it before you can sign in.",
    }


@router.post("/setup", status_code=201)
async def run_setup(body: SetupIn):
    """API-only alternative to the first registration (e.g. for automated installs)."""
    session = await initialise(
        body.organization_name,
        body.currency,
        body.timezone,
        body.admin.username,
        body.admin.full_name,
        body.admin.email,
        body.admin.password,
    )
    return session
