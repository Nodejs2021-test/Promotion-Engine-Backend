"""FastAPI dependencies: authentication (JWT or API key) and permission checks."""

from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.common.utils import utcnow
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import has_permission
from app.core.security import hash_api_key
from app.modules.roles.service import role_permissions

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

_unauthorised = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def _user_from_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise _unauthorised from None
    user = await get_db().users.find_one({"username": payload.get("sub")}, {"password_hash": 0})
    if not user or not user.get("active", True):
        raise _unauthorised
    # Permissions are resolved from the roles collection on every request, so role changes apply at once.
    user["permissions"] = await role_permissions(user.get("role", ""))
    return user


async def get_current_user(token: Annotated[str | None, Depends(oauth2_scheme)]) -> dict:
    if not token:
        raise _unauthorised
    return await _user_from_token(token)


CurrentUser = Annotated[dict, Depends(get_current_user)]


def require_permission(permission: str):
    async def checker(user: CurrentUser) -> dict:
        if not has_permission(user, permission):
            raise HTTPException(status_code=403, detail=f"Your role does not allow '{permission}'")
        return user

    return checker


async def get_order_principal(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> dict:
    """Sales orders are accepted from an integration API key or a logged-in user allowed to record them."""
    if x_api_key:
        key = await get_db().api_keys.find_one({"key_hash": hash_api_key(x_api_key), "active": True})
        if not key:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        await get_db().api_keys.update_one({"_id": key["_id"]}, {"$set": {"last_used_at": utcnow()}})
        return {"type": "API_KEY", "name": key["name"], "key_id": key["key_id"]}
    if not token:
        raise _unauthorised
    user = await _user_from_token(token)
    if not has_permission(user, "salesorder:write"):
        raise HTTPException(status_code=403, detail="Your role does not allow 'salesorder:write'")
    return {"type": "USER", "name": user["username"]}


Viewer = Annotated[dict, Depends(require_permission("view"))]

OrderWriter = Annotated[dict, Depends(require_permission("salesorder:write"))]

CampaignWriter = Annotated[dict, Depends(require_permission("campaign:write"))]

CampaignApprover = Annotated[dict, Depends(require_permission("campaign:approve"))]

UserAdmin = Annotated[dict, Depends(require_permission("users:manage"))]

KeyAdmin = Annotated[dict, Depends(require_permission("apikeys:manage"))]
