"""Collection `$jsonSchema` validators and indexes, applied at start-up."""

import logging

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import CollectionInvalid, OperationFailure

from app.core.database import check_connection, get_db
from app.modules.pricing.model import CAP_TREATMENTS, COMPARISON_MODES, QUANTITY_BASES, RULE_FAMILIES

log = logging.getLogger("promotion_engine.db")

_str = {"bsonType": "string"}

_nstr = {"bsonType": ["string", "null"]}

_int = {"bsonType": ["int", "long"]}


_date = {"bsonType": "date"}


_str_array = {"bsonType": "array", "items": _str}


def _schema(required: list[str], props: dict) -> dict:
    return {"$jsonSchema": {"bsonType": "object", "required": required, "properties": props}}


VALIDATORS: dict[str, dict] = {
    "app_settings": _schema(
        ["organization_name", "currency", "timezone"],
        {
            "organization_name": _str,
            "currency": {"bsonType": "string", "pattern": "^[A-Z]{3}$"},
            "timezone": _str,
        },
    ),
    "roles": _schema(
        ["role", "label", "permissions"],
        {
            "role": {"bsonType": "string", "pattern": "^[A-Z][A-Z0-9_]*$"},
            "label": _str,
            "permissions": _str_array,
            "system": {"bsonType": "bool"},
        },
    ),
    "users": _schema(
        ["username", "full_name", "role", "password_hash", "active"],
        {
            "username": _str,
            "full_name": _str,
            "email": _nstr,
            "role": _str,
            "password_hash": _str,
            "active": {"bsonType": "bool"},
        },
    ),
    "api_keys": _schema(
        ["key_id", "name", "key_hash", "active"],
        {
            "key_id": _str,
            "name": _str,
            "key_hash": _str,
            "active": {"bsonType": "bool"},
        },
    ),
    "campaigns": _schema(
        ["campaign_id", "code", "name", "start_date", "end_date", "approval_status", "priority"],
        {
            "campaign_id": _str,
            "code": _str,
            "name": _str,
            "description": _nstr,
            "start_date": {"bsonType": "date"},
            "end_date": {"bsonType": "date"},
            "approval_status": {"enum": ["DRAFT", "PENDING_APPROVAL", "APPROVED"]},
            "enabled": {"bsonType": "bool"},
            "priority": {"bsonType": ["int", "long"]},
            "eligibility": {
                "bsonType": "object",
                "properties": {
                    k: _str_array for k in ("customer_ids", "customer_groups", "channels", "skus", "item_groups")
                },
            },
        },
    ),
    "campaign_rules": _schema(
        ["rule_id", "campaign_id", "name", "priority", "active", "conditions"],
        {
            "rule_id": _str,
            "campaign_id": _str,
            "name": _str,
            "priority": {"bsonType": ["int", "long"]},
            "active": {"bsonType": "bool"},
            "start_date": {"bsonType": ["date", "null"]},
            "end_date": {"bsonType": ["date", "null"]},
            "conditions": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["field", "operator", "value"],
                    "properties": {"field": _str, "operator": _str},
                },
            },
            "family": {"enum": [None, *RULE_FAMILIES]},
            "comparison_mode": {"enum": [None, *COMPARISON_MODES]},
            "cap_treatment": {"enum": [None, *CAP_TREATMENTS]},
            "quantity_basis": {"enum": [None, *QUANTITY_BASES]},
            "version": {"bsonType": ["int", "long"], "minimum": 1},
            "items": {"bsonType": "array"},
            "tiers": {"bsonType": "array"},
            "action": {
                "bsonType": ["object", "null"],
                "required": ["action_type", "value"],
                "properties": {
                    "action_type": {"enum": ["PERCENTAGE", "FIXED_DISCOUNT", "PROMOTIONAL_PRICE", "FIXED_UNIT_PRICE"]},
                    "value": {"bsonType": "decimal"},
                },
            },
        },
    ),
    "integration_mappings": _schema(["profile", "updated_by", "updated_at"], {"profile": {"bsonType": "object"}}),
    "audit_logs": _schema(
        ["entity_type", "entity_id", "action", "user", "timestamp"],
        {
            "entity_type": _str,
            "entity_id": _str,
            "action": _str,
            "user": _str,
            "timestamp": _date,
        },
    ),
    "counters": _schema(["seq"], {"seq": _int}),
    "uploads": _schema(
        ["content_type", "size", "data", "uploaded_by", "uploaded_at"],
        {
            "content_type": {"bsonType": "string", "pattern": "^image/"},
            "size": _int,
            "data": {"bsonType": "binData"},
            "uploaded_by": _str,
            "uploaded_at": _date,
        },
    ),
}


async def ensure_collections() -> None:
    """Create every collection with its JSON-schema validator (or update the validator if it exists)."""
    db = get_db()
    existing = set(await db.list_collection_names())
    for name, validator in VALIDATORS.items():
        if name in existing:
            await db.command(
                {"collMod": name, "validator": validator, "validationLevel": "moderate", "validationAction": "error"}
            )
        else:
            try:
                await db.create_collection(
                    name, validator=validator, validationLevel="moderate", validationAction="error"
                )
            except CollectionInvalid:
                pass


async def ensure_indexes() -> None:
    raw = get_db()

    class _Safe:
        """create_index that keeps an index already made with other options (e.g. a stricter unique one)."""

        def __init__(self, coll):
            self.coll = coll

        async def create_index(self, keys, **kwargs):
            try:
                return await self.coll.create_index(keys, **kwargs)
            except OperationFailure as e:
                if e.code not in (85, 86):  # IndexOptionsConflict, IndexKeySpecsConflict
                    raise
                log.warning(
                    "Kept existing index on %s %s (other options than requested): %s",
                    self.coll.name,
                    keys,
                    e.details.get("errmsg") if e.details else e,
                )
                return None

    class _SafeDb:
        def __getattr__(self, name):
            return _Safe(raw[name])

    db = _SafeDb()
    await db.roles.create_index("role", unique=True)
    await db.users.create_index("username", unique=True)
    await db.users.create_index("role")
    await db.api_keys.create_index("key_hash", unique=True)
    await db.api_keys.create_index("key_id", unique=True)
    await db.campaigns.create_index("campaign_id", unique=True)
    await db.campaigns.create_index("code", unique=True)
    await db.campaigns.create_index([("approval_status", ASCENDING), ("enabled", ASCENDING)])
    await db.campaign_rules.create_index("rule_id", unique=True)
    await db.campaign_rules.create_index([("campaign_id", ASCENDING), ("priority", ASCENDING)])
    await db.sales_orders.create_index([("source", ASCENDING), ("sales_order_id", ASCENDING)], unique=True)
    await db.sales_orders.create_index([("last_received_at", DESCENDING)])
    await db.sales_orders.create_index("customer_id")
    await db.audit_logs.create_index([("entity_type", ASCENDING), ("entity_id", ASCENDING), ("timestamp", DESCENDING)])
    await db.audit_logs.create_index([("timestamp", DESCENDING)])


async def init_database() -> dict:
    info = await check_connection()
    await ensure_collections()
    await ensure_indexes()
    log.info("MongoDB %s ready, database '%s'", info["version"], info["database"])
    return info
