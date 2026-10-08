"""Integration API keys for NetSuite, Power Apps and other callers of the Pricing API."""

from fastapi import APIRouter, HTTPException

from app.api.dependencies import KeyAdmin
from app.common.utils import serialize, utcnow
from app.core.database import get_db, next_id
from app.core.security import generate_api_key, hash_api_key
from app.modules.api_keys.schema import ApiKeyIn
from app.modules.audit.service import write_audit

router = APIRouter(tags=["API keys"])


@router.get("/api-keys")
async def list_api_keys(_: KeyAdmin):
    return [
        serialize({k: v for k, v in key.items() if k != "key_hash"})
        async for key in get_db().api_keys.find().sort("created_at", -1)
    ]


@router.post("/api-keys", status_code=201)
async def create_api_key(body: ApiKeyIn, admin: KeyAdmin):
    raw = generate_api_key()
    doc = {
        "key_id": await next_id("KEY"),
        "name": body.name,
        "key_hash": hash_api_key(raw),
        "prefix": raw[:10],
        "active": True,
        "created_at": utcnow(),
        "created_by": admin["username"],
        "last_used_at": None,
    }
    await get_db().api_keys.insert_one(doc)
    await write_audit("API_KEY", doc["key_id"], "CREATE", admin["username"], details=body.name)
    out = serialize({k: v for k, v in doc.items() if k != "key_hash"})
    out["api_key"] = raw  # shown only once
    return out


@router.post("/api-keys/{key_id}/revoke")
async def revoke_api_key(key_id: str, admin: KeyAdmin):
    res = await get_db().api_keys.update_one({"key_id": key_id}, {"$set": {"active": False, "revoked_at": utcnow()}})
    if not res.matched_count:
        raise HTTPException(404, f"API key {key_id} not found")
    await write_audit("API_KEY", key_id, "REVOKE", admin["username"])
    return {"key_id": key_id, "active": False}
