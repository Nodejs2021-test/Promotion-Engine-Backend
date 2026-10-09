"""Programme eligibility records: who may take part in Accelerate, Make the Switch and Monthly Promotions.

A record never holds a price: it only allows a rule with that programme to be evaluated (Item Scope Spec §5).
Records are inactivated or end-dated, never deleted, so history stays auditable (§12).
"""

from datetime import date

from fastapi import HTTPException

from app.common.utils import serialize, to_date, to_dt, utcnow
from app.core.database import get_db, next_id
from app.modules.audit.service import write_audit
from app.modules.eligibility.schema import EligibilityIn
from app.modules.pricing.model import EligibilityRecord


def _fields(body: EligibilityIn) -> dict:
    data = body.model_dump(exclude={"valid_from", "valid_to"})
    data.update(valid_from=to_dt(body.valid_from), valid_to=to_dt(body.valid_to))
    return data


def _overlaps(a_from: date, a_to: date | None, b_from: date, b_to: date | None) -> bool:
    return a_from <= (b_to or date.max) and b_from <= (a_to or date.max)


async def _check_overlap(body: EligibilityIn, eligibility_id: str | None = None) -> None:
    """No two active records for the same programme and scope value may overlap in time (§12)."""
    if not body.active:
        return
    query = {"programme_code": body.programme_code, "scope": body.scope, "active": True}
    async for doc in get_db().programme_eligibility.find(query):
        if doc["eligibility_id"] == eligibility_id or doc["value"].casefold() != body.value.casefold():
            continue
        if _overlaps(body.valid_from, body.valid_to, to_date(doc["valid_from"]), to_date(doc.get("valid_to"))):
            raise HTTPException(
                409, f"Overlaps active record {doc['eligibility_id']} for the same programme and {body.scope}"
            )


async def list_records(programme: str | None = None) -> list[dict]:
    query = {"programme_code": programme} if programme else {}
    cursor = get_db().programme_eligibility.find(query).sort([("programme_code", 1), ("scope", 1), ("value", 1)])
    return [serialize(d) async for d in cursor]


async def create(body: EligibilityIn, username: str) -> dict:
    await _check_overlap(body)
    now = utcnow()
    doc = {
        "eligibility_id": await next_id("ELIG"),
        **_fields(body),
        "created_by": username,
        "created_at": now,
        "updated_by": username,
        "updated_at": now,
    }
    await get_db().programme_eligibility.insert_one(doc)
    await write_audit("ELIGIBILITY", doc["eligibility_id"], "CREATE", username, after=doc)
    return serialize(doc)


async def update(eligibility_id: str, body: EligibilityIn, username: str) -> dict:
    db = get_db()
    before = await db.programme_eligibility.find_one({"eligibility_id": eligibility_id})
    if not before:
        raise HTTPException(404, f"Eligibility record {eligibility_id} not found")
    await _check_overlap(body, eligibility_id)
    await db.programme_eligibility.update_one(
        {"eligibility_id": eligibility_id}, {"$set": {**_fields(body), "updated_by": username, "updated_at": utcnow()}}
    )
    after = await db.programme_eligibility.find_one({"eligibility_id": eligibility_id})
    await write_audit("ELIGIBILITY", eligibility_id, "UPDATE", username, before, after)
    return serialize(after)


async def load_engine_records() -> list[EligibilityRecord]:
    """Active records for the rules engine (dates are checked by the engine against the order date)."""
    return [
        EligibilityRecord(
            programme_code=d["programme_code"],
            scope=d["scope"],
            value=d["value"],
            eligible=d["eligibility_mode"] == "ELIGIBLE",
            valid_from=to_date(d.get("valid_from")),
            valid_to=to_date(d.get("valid_to")),
            eligibility_id=d["eligibility_id"],
        )
        async for d in get_db().programme_eligibility.find({"active": True})
    ]
