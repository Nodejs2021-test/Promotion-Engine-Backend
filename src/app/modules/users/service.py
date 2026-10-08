"""User representation returned by the API."""

from app.common.utils import serialize
from app.core.database import get_db
from app.modules.roles.service import role_permissions


async def user_out(u: dict) -> dict:
    out = serialize({k: v for k, v in u.items() if k not in ("password_hash", "permissions")})
    out["permissions"] = u["permissions"] if "permissions" in u else await role_permissions(u.get("role", ""))
    role = await get_db().roles.find_one({"role": u.get("role")}, {"label": 1})
    out["role_label"] = role["label"] if role else u.get("role")
    return out
