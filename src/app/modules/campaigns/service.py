"""Campaigns and their rules: create, edit, approve, order, and load them for the rules engine.

Lifecycle (``approval_status``): DRAFT → PENDING_APPROVAL → APPROVED; a rejection returns the campaign to DRAFT
with the reviewer's comment. Changing an approved or submitted campaign (or its rules) returns it to DRAFT, so
only what a reviewer approved is ever applied. ``enabled`` switches an approved campaign off and on.

The status shown to users is derived: Draft, Pending Approval, Approved (starts later), Active, Expired, Disabled.
Only Approved + enabled campaigns whose period includes the order date are applied to sales orders.
"""

from datetime import date
from decimal import Decimal

from fastapi import HTTPException

from app.common.utils import search_regex, serialize, to_date, to_dt, today, utcnow
from app.core import app_settings
from app.core.database import get_db, next_id
from app.modules.audit.service import write_audit
from app.modules.campaigns.schema import CampaignIn, RuleIn
from app.modules.pricing import model
from app.modules.pricing.conditions import describe, min_quantity
from app.modules.pricing.discount import describe_outcome
from app.modules.pricing.eligibility import specificity_rank
from app.modules.pricing.priority import check_order

DRAFT, PENDING, APPROVED = "DRAFT", "PENDING_APPROVAL", "APPROVED"
STATUSES = {
    "DRAFT": "Draft",
    "PENDING_APPROVAL": "Pending Approval",
    "APPROVED": "Approved",
    "ACTIVE": "Active",
    "EXPIRED": "Expired",
    "DISABLED": "Disabled",
}


def effective_status(doc: dict, on: date | None = None) -> str:
    on = on or today()
    if doc["approval_status"] != APPROVED:
        return doc["approval_status"]
    if not doc.get("enabled", True):
        return "DISABLED"
    if to_date(doc["end_date"]) < on:
        return "EXPIRED"
    if to_date(doc["start_date"]) > on:
        return "APPROVED"
    return "ACTIVE"


# --------------------------------------------------------------------------- read
def _dec(v) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


def _rule_model(r: dict) -> model.Rule:
    """A stored rule as the engine's Rule. Rules saved before v2.0 get the spec defaults (Everyday, Best Price,
    apply cap, line quantity, version 1)."""
    action = r.get("action")
    bonus = r.get("bonus")
    return model.Rule(
        rule_id=r["rule_id"],
        name=r["name"],
        priority=r.get("priority", 1),
        active=r.get("active", True),
        start_date=to_date(r.get("start_date")),
        end_date=to_date(r.get("end_date")),
        conditions=tuple(
            model.Condition(
                c["field"],
                c["operator"],
                Decimal(str(c["value"])) if c["operator"] in model.NUMBER_OPERATORS else c["value"],
            )
            for c in r.get("conditions") or []
        ),
        action=model.Action(action["action_type"], Decimal(str(action["value"]))) if action else None,
        family=r.get("family") or "EVERYDAY",
        rule_type=r.get("rule_type") or "QUANTITY_TIER",
        comparison_mode=r.get("comparison_mode") or "BEST_PRICE",
        cap_treatment=r.get("cap_treatment") or "APPLY_CAP",
        version=r.get("version") or 1,
        programme_code=r.get("programme_code"),
        promotion_code=r.get("promotion_code"),
        quantity_basis=r.get("quantity_basis") or "LINE_QUANTITY",
        mixed_pool_id=r.get("mixed_pool_id"),
        required_shipper_multiple=_dec(r.get("required_shipper_multiple")) or Decimal(1),
        items=tuple(
            model.RuleItem(
                item_id=i.get("item_id"),
                item_code=i.get("item_code"),
                item_name=i.get("item_name"),
                include=i.get("include", True),
                mixed_pool_id=i.get("mixed_pool_id"),
                role=i.get("role") or "STANDARD",
                rate=_dec(i.get("rate")),
                active=i.get("active", True),
            )
            for i in r.get("items") or []
        ),
        tiers=tuple(
            model.Tier(
                position=t["position"],
                min_quantity=_dec(t["min_quantity"]),
                max_quantity=_dec(t.get("max_quantity")),
                discount_percentage=_dec(t.get("discount_percentage")),
                fixed_unit_rate=_dec(t.get("fixed_unit_rate")),
                tier_id=f"{r['rule_id']}-T{t['position']}",
            )
            for t in r.get("tiers") or []
        ),
        rate_overrides=tuple(
            model.RateOverride(o["item_id"], o["tier_position"], _dec(o["discount_percentage"]))
            for o in r.get("rate_overrides") or []
        ),
        max_discount_percentage=_dec(r.get("max_discount_percentage")),
        currency=r.get("currency"),
        max_uses_total=r.get("max_uses_total"),
        max_uses_per_customer=r.get("max_uses_per_customer"),
        bonus=model.Bonus(
            _dec(bonus["buy_quantity"]),
            _dec(bonus["bonus_quantity"]),
            bonus.get("repeatable", True),
            bonus.get("permitted_family", "BASE"),
        )
        if bonus
        else None,
    )


def _campaign_model(c: dict, rules: list[dict]) -> model.Campaign:
    e = c.get("eligibility") or {}
    return model.Campaign(
        campaign_id=c["campaign_id"],
        code=c["code"],
        name=c["name"],
        priority=c.get("priority", 1),
        start_date=to_date(c["start_date"]),
        end_date=to_date(c["end_date"]),
        approved=c["approval_status"] == APPROVED,
        enabled=c.get("enabled", True),
        customer_ids=frozenset(e.get("customer_ids") or []),
        customer_groups=frozenset(e.get("customer_groups") or []),
        channels=frozenset(e.get("channels") or []),
        skus=frozenset(e.get("skus") or []),
        item_groups=frozenset(e.get("item_groups") or []),
        rules=tuple(_rule_model(r) for r in rules),
    )


def rule_out(r: dict, currency: str = "") -> dict:
    """A rule with its plain-language WHEN / THEN text."""
    m = _rule_model(r)
    return {
        **serialize(r),
        "family": m.family,
        "rule_type": m.rule_type,
        "comparison_mode": m.comparison_mode,
        "cap_treatment": m.cap_treatment,
        "quantity_basis": m.quantity_basis,
        "version": m.version,
        "when": [describe(c) for c in m.conditions],
        "then": describe_outcome(m, currency),
        "min_quantity": min_quantity(m.conditions),
    }


def campaign_out(doc: dict, rule_count: int | None = None, usage: dict | None = None) -> dict:
    out = serialize(doc)
    out["status"] = effective_status(doc)
    if rule_count is not None:
        out["rule_count"] = rule_count
    if usage is not None:
        out["applied_rule_count"] = usage.get("rules", 0)
        out["applied_order_count"] = usage.get("orders", 0)
    return out


async def _applied_usage(rule_ids: dict[str, set[str]]) -> dict[str, dict]:
    """Per campaign, from the stored pricing result of every non-cancelled sales order: how many of its existing
    rules won at least one line, and on how many sales orders."""
    pipeline = [
        {"$match": {"cancelled": {"$ne": True}, "pricing.items": {"$exists": True}}},
        {"$unwind": "$pricing.items"},
        {"$match": {"pricing.items.campaignId": {"$ne": None}, "pricing.items.ruleId": {"$ne": None}}},
        {
            "$group": {
                "_id": "$pricing.items.campaignId",
                "rules": {"$addToSet": "$pricing.items.ruleId"},
                "orders": {"$addToSet": "$_id"},
            }
        },
    ]
    out: dict[str, dict] = {}
    async for d in await get_db().sales_orders.aggregate(pipeline):
        out[d["_id"]] = {"rules": len(set(d["rules"]) & rule_ids.get(d["_id"], set())), "orders": len(d["orders"])}
    return out


async def _get(campaign_id: str) -> dict:
    doc = await get_db().campaigns.find_one({"campaign_id": campaign_id})
    if not doc:
        raise HTTPException(404, f"Campaign {campaign_id} not found")
    return doc


async def _rules(campaign_id: str) -> list[dict]:
    cursor = get_db().campaign_rules.find({"campaign_id": campaign_id}).sort([("priority", 1), ("rule_id", 1)])
    return [r async for r in cursor]


async def list_campaigns(q: str | None, status: str | None) -> list[dict]:
    db = get_db()
    query: dict = {}
    if q:
        rx = search_regex(q)
        query["$or"] = [{"name": rx}, {"code": rx}, {"campaign_id": rx}]
    rule_ids: dict[str, set[str]] = {}
    families: dict[str, set[str]] = {}
    async for r in db.campaign_rules.find({}, {"campaign_id": 1, "rule_id": 1, "family": 1}):
        rule_ids.setdefault(r["campaign_id"], set()).add(r["rule_id"])
        families.setdefault(r["campaign_id"], set()).add(r.get("family") or "EVERYDAY")
    usage = await _applied_usage(rule_ids)
    docs = [d async for d in db.campaigns.find(query)]
    docs.sort(key=lambda d: (d.get("priority", 0), d["campaign_id"]))
    out = [
        {
            **campaign_out(d, len(rule_ids.get(d["campaign_id"], ())), usage.get(d["campaign_id"], {})),
            "families": sorted(families.get(d["campaign_id"], ())),
        }
        for d in docs
    ]
    return [c for c in out if not status or c["status"] == status]


async def get_campaign(campaign_id: str) -> dict:
    doc = await _get(campaign_id)
    rules = [rule_out(r, app_settings.currency() or "") for r in await _rules(campaign_id)]
    return {**campaign_out(doc, len(rules)), "rules": rules}


async def load_engine_campaigns() -> list[model.Campaign]:
    """Approved and enabled campaigns with their rules (the engine checks the dates)."""
    db = get_db()
    docs = [d async for d in db.campaigns.find({"approval_status": APPROVED, "enabled": True})]
    ids = [d["campaign_id"] for d in docs]
    rules: dict[str, list[dict]] = {i: [] for i in ids}
    async for r in db.campaign_rules.find({"campaign_id": {"$in": ids}}).sort([("priority", 1), ("rule_id", 1)]):
        rules[r["campaign_id"]].append(r)
    return [_campaign_model(d, rules[d["campaign_id"]]) for d in docs]


async def check_order_view(on: date | None = None) -> dict:
    """The rules of the campaigns that are active on ``on``, in the order the engine checks them, and why."""
    on = on or today()
    active = [c for c in await load_engine_campaigns() if c.start_date <= on <= c.end_date]
    rows = []
    for c, r in check_order(active):
        if not r.active:
            continue
        rows.append(
            {
                "position": len(rows) + 1,
                "campaign_id": c.campaign_id,
                "campaign_name": c.name,
                "campaign_priority": c.priority,
                "family": r.family,
                "rule_id": r.rule_id,
                "rule_name": r.name,
                "rule_priority": r.priority,
                "rule_version": r.version,
                "specificity": specificity_rank(c, r),
                "min_quantity": min_quantity(r.conditions),
                "when": [describe(x) for x in r.conditions],
                "then": describe_outcome(r),
            }
        )
    return {"date": on.isoformat(), "rules": rows}


# --------------------------------------------------------------------------- campaign changes
def _campaign_fields(body: CampaignIn) -> dict:
    return {
        "name": body.name,
        "code": body.code,
        "description": body.description,
        "start_date": to_dt(body.start_date),
        "end_date": to_dt(body.end_date),
        "eligibility": {
            "customer_ids": body.customer_ids,
            "customer_groups": body.customer_groups,
            "channels": body.channels,
            "skus": body.skus,
            "item_groups": body.item_groups,
        },
    }


async def _unique_code(code: str, campaign_id: str | None = None) -> None:
    other = await get_db().campaigns.find_one({"code": code, "campaign_id": {"$ne": campaign_id}}, {"_id": 1})
    if other:
        raise HTTPException(409, f"Campaign code {code} is already used")


def _back_to_draft(doc: dict) -> dict:
    """Fields that return a submitted / approved campaign to Draft after a change."""
    if doc["approval_status"] in (PENDING, APPROVED):
        return {"approval_status": DRAFT, "approved_by": None, "approved_at": None}
    return {}


async def create_campaign(body: CampaignIn, username: str) -> dict:
    db = get_db()
    await _unique_code(body.code)
    last = await db.campaigns.find_one({}, {"priority": 1}, sort=[("priority", -1)])
    now = utcnow()
    doc = {
        "campaign_id": await next_id("CAM", 3),
        **_campaign_fields(body),
        "approval_status": DRAFT,
        "enabled": True,
        "priority": (last or {}).get("priority", 0) + 1,
        "created_by": username,
        "created_at": now,
        "updated_by": username,
        "updated_at": now,
    }
    await db.campaigns.insert_one(doc)
    await write_audit("CAMPAIGN", doc["campaign_id"], "CREATE", username, after=doc)
    return await get_campaign(doc["campaign_id"])


async def update_campaign(campaign_id: str, body: CampaignIn, username: str) -> dict:
    before = await _get(campaign_id)
    await _unique_code(body.code, campaign_id)
    changes = {**_campaign_fields(body), **_back_to_draft(before), "updated_by": username, "updated_at": utcnow()}
    await get_db().campaigns.update_one({"campaign_id": campaign_id}, {"$set": changes})
    after = await _get(campaign_id)
    details = "Changed after approval: back to Draft" if before["approval_status"] != after["approval_status"] else None
    await write_audit("CAMPAIGN", campaign_id, "UPDATE", username, before, after, details)
    return await get_campaign(campaign_id)


async def delete_campaign(campaign_id: str, username: str) -> None:
    doc = await _get(campaign_id)
    if doc.get("approved_once"):
        raise HTTPException(409, "A campaign that was approved cannot be deleted; disable it instead")
    await get_db().campaign_rules.delete_many({"campaign_id": campaign_id})
    await get_db().campaigns.delete_one({"campaign_id": campaign_id})
    await write_audit("CAMPAIGN", campaign_id, "DELETE", username, before=doc)


async def _transition(
    campaign_id: str, username: str, allowed: tuple[str, ...], changes: dict, action: str, details=None
):
    before = await _get(campaign_id)
    if before["approval_status"] not in allowed:
        label = STATUSES.get(before["approval_status"], before["approval_status"])
        raise HTTPException(409, f"Campaign {campaign_id} is {label}; it cannot be {action.lower().replace('_', ' ')}")
    await get_db().campaigns.update_one(
        {"campaign_id": campaign_id}, {"$set": {**changes, "updated_by": username, "updated_at": utcnow()}}
    )
    await write_audit("CAMPAIGN", campaign_id, action, username, before, await _get(campaign_id), details)
    return await get_campaign(campaign_id)


async def submit(campaign_id: str, username: str) -> dict:
    doc = await _get(campaign_id)
    if not any(r.get("active", True) for r in await _rules(campaign_id)):
        raise HTTPException(422, "Add at least one active rule before submitting the campaign")
    if to_date(doc["end_date"]) < today():
        raise HTTPException(422, "The campaign has already ended; change its dates first")
    changes = {"approval_status": PENDING, "submitted_by": username, "submitted_at": utcnow()}
    return await _transition(campaign_id, username, (DRAFT,), changes, "SUBMIT")


async def approve(campaign_id: str, username: str, comment: str | None) -> dict:
    changes = {
        "approval_status": APPROVED,
        "approved_by": username,
        "approved_at": utcnow(),
        "approved_once": True,
        "last_rejection": None,
    }
    return await _transition(campaign_id, username, (PENDING,), changes, "APPROVE", comment)


async def reject(campaign_id: str, username: str, comment: str | None) -> dict:
    changes = {"approval_status": DRAFT, "last_rejection": {"by": username, "at": utcnow(), "comment": comment}}
    return await _transition(campaign_id, username, (PENDING,), changes, "REJECT", comment)


async def set_enabled(campaign_id: str, enabled: bool, username: str) -> dict:
    action = "ENABLE" if enabled else "DISABLE"
    allowed = (DRAFT, PENDING, APPROVED)
    return await _transition(campaign_id, username, allowed, {"enabled": enabled}, action)


async def reorder_campaigns(ids: list[str], username: str) -> list[dict]:
    """Put the given campaigns in this order, reusing the priority numbers they already hold."""
    db = get_db()
    docs = {d["campaign_id"]: d async for d in db.campaigns.find({"campaign_id": {"$in": ids}})}
    missing = sorted(set(ids) - set(docs))
    if missing or len(set(ids)) != len(ids):
        raise HTTPException(422, f"Unknown or repeated campaign(s): {', '.join(missing) or 'duplicates'}")
    slots = sorted(d.get("priority", 0) for d in docs.values())
    for cid, priority in zip(ids, slots, strict=True):
        await db.campaigns.update_one({"campaign_id": cid}, {"$set": {"priority": priority}})
    await write_audit(
        "CAMPAIGN_ORDER",
        "campaigns",
        "REORDER",
        username,
        {"order": sorted(ids, key=lambda i: docs[i].get("priority", 0))},
        {"order": ids},
    )
    return await list_campaigns(None, None)


# --------------------------------------------------------------------------- rules
def _rule_fields(body: RuleIn) -> dict:
    data = body.model_dump(exclude={"start_date", "end_date"})
    data["start_date"] = to_dt(body.start_date)
    data["end_date"] = to_dt(body.end_date)
    return data


def _check_audience(campaign: dict, body: RuleIn) -> None:
    """Rules use only controlled values (Item Scope Spec §11.1); every executable Monthly Promotion needs an
    explicit audience (Functional Spec §4.2, §6.6)."""
    controlled = app_settings.controlled_values()
    for c in body.conditions:
        allowed = {v.casefold() for v in controlled.get(c.field) or []}
        values = c.value if isinstance(c.value, list) else [c.value]
        bad = [v for v in values if allowed and str(v).casefold() not in allowed]
        if bad:
            raise HTTPException(
                422, f"{model.FIELD_LABELS[c.field]}: {', '.join(map(str, bad))} is not a controlled value"
            )
    e = campaign.get("eligibility") or {}
    if body.family == "MONTHLY_PROMO" and not (
        body.has_audience() or e.get("customer_ids") or e.get("customer_groups") or e.get("channels")
    ):
        raise HTTPException(
            422,
            "A Monthly Promotion needs an explicit audience: a Customer, Customer Group, Banner, Marketing Flag "
            "or Channel condition (on the rule or the campaign)",
        )


async def _rule(campaign_id: str, rule_id: str) -> dict:
    r = await get_db().campaign_rules.find_one({"campaign_id": campaign_id, "rule_id": rule_id})
    if not r:
        raise HTTPException(404, f"Rule {rule_id} not found in campaign {campaign_id}")
    return r


async def _touch(campaign: dict, username: str) -> None:
    """A rule changed: the campaign returns to Draft if it was submitted or approved."""
    back = _back_to_draft(campaign)
    await get_db().campaigns.update_one(
        {"campaign_id": campaign["campaign_id"]}, {"$set": {**back, "updated_by": username, "updated_at": utcnow()}}
    )
    if back:
        await write_audit(
            "CAMPAIGN", campaign["campaign_id"], "BACK_TO_DRAFT", username, details="A rule changed after submission"
        )


async def add_rule(campaign_id: str, body: RuleIn, username: str) -> dict:
    campaign = await _get(campaign_id)
    _check_audience(campaign, body)
    db = get_db()
    last = await db.campaign_rules.find_one({"campaign_id": campaign_id}, {"priority": 1}, sort=[("priority", -1)])
    now = utcnow()
    doc = {
        "rule_id": await next_id("RULE", 3),
        "campaign_id": campaign_id,
        **_rule_fields(body),
        "priority": (last or {}).get("priority", 0) + 1,
        "version": 1,
        "created_by": username,
        "created_at": now,
        "updated_by": username,
        "updated_at": now,
    }
    await db.campaign_rules.insert_one(doc)
    await write_audit("CAMPAIGN_RULE", doc["rule_id"], "CREATE", username, after=doc, details=f"Campaign {campaign_id}")
    await _touch(campaign, username)
    return await get_campaign(campaign_id)


async def update_rule(campaign_id: str, rule_id: str, body: RuleIn, username: str) -> dict:
    campaign = await _get(campaign_id)
    _check_audience(campaign, body)
    before = await _rule(campaign_id, rule_id)
    # Every change is a new executable version (Data Spec §3); the audit log keeps each version's content.
    await get_db().campaign_rules.update_one(
        {"rule_id": rule_id},
        {
            "$set": {
                **_rule_fields(body),
                "version": (before.get("version") or 1) + 1,
                "updated_by": username,
                "updated_at": utcnow(),
            }
        },
    )
    after = await _rule(campaign_id, rule_id)
    await write_audit("CAMPAIGN_RULE", rule_id, "UPDATE", username, before, after, f"Campaign {campaign_id}")
    await _touch(campaign, username)
    return await get_campaign(campaign_id)


async def delete_rule(campaign_id: str, rule_id: str, username: str) -> dict:
    campaign = await _get(campaign_id)
    before = await _rule(campaign_id, rule_id)
    await get_db().campaign_rules.delete_one({"rule_id": rule_id})
    await write_audit("CAMPAIGN_RULE", rule_id, "DELETE", username, before=before, details=f"Campaign {campaign_id}")
    await _touch(campaign, username)
    return await get_campaign(campaign_id)


async def reorder_rules(campaign_id: str, ids: list[str], username: str) -> dict:
    campaign = await _get(campaign_id)
    existing = [r["rule_id"] for r in await _rules(campaign_id)]
    if sorted(ids) != sorted(existing):
        raise HTTPException(422, "Send every rule of the campaign exactly once")
    for priority, rid in enumerate(ids, start=1):
        await get_db().campaign_rules.update_one({"rule_id": rid}, {"$set": {"priority": priority}})
    await write_audit("CAMPAIGN", campaign_id, "REORDER_RULES", username, {"order": existing}, {"order": ids})
    await _touch(campaign, username)
    return await get_campaign(campaign_id)
