"""Sales orders: normalised sales order -> pricing (campaign rules) -> stored record -> list / details.

The normalised order (``modules/sales_orders/schema.py``) comes from ``POST /sales-orders``, ``POST /pricing/evaluate``
or the NetSuite field mapping (``integrations/netsuite/mapping.py``). Every order is priced against the approved campaigns,
stored as received together with its pricing result, and the pricing result is returned to the sender.
"""

from decimal import Decimal

from fastapi import HTTPException
from pydantic import ValidationError
from pymongo import ReturnDocument

from app.common.utils import search_regex, serialize, to_dt, today, utcnow
from app.core import app_settings
from app.core.database import get_db
from app.integrations.netsuite.mapping import MappingError, get_profile, map_sales_order, with_new_prices
from app.modules.audit.service import write_audit
from app.modules.pricing import service as order_pricing
from app.modules.sales_orders.schema import CancellationIn, NormalisedSalesOrder, SalesOrderLine


def _unit_price(ln: SalesOrderLine) -> Decimal | None:
    """The price the order charges for one unit: the current price, else the base price."""
    return ln.current_unit_price if ln.current_unit_price is not None else ln.base_unit_price


def _line_out(ln: SalesOrderLine) -> dict:
    unit = _unit_price(ln)
    return {
        "lineId": ln.line_id,
        "itemCode": ln.item_code,
        "itemId": ln.item_id,
        "itemName": ln.item_name,
        "quantity": ln.quantity,
        "currentUnitPrice": ln.current_unit_price,
        "baseUnitPrice": ln.base_unit_price,
        "unitPrice": unit,
        "lineTotal": unit * ln.quantity if unit is not None else None,
    }


def order_total(order: NormalisedSalesOrder) -> Decimal:
    return sum((_unit_price(ln) * ln.quantity for ln in order.lines if _unit_price(ln) is not None), Decimal(0))


def _pricing_fields(pricing: dict) -> dict:
    totals = pricing["totals"]
    return {
        "pricing": pricing,
        "pricing_status": pricing["pricingStatus"],
        "final_total": Decimal(str(totals["final"])),
        "savings": Decimal(str(totals["savings"])),
        "promotion_line_count": totals["promotionLines"],
        # Rules applied to this order: counts promotion-code usage.
        "applied_rule_ids": sorted(
            {i["ruleId"] for i in pricing["items"] if i.get("promotionApplied") and i.get("ruleId")}
        ),
        "last_priced_at": utcnow(),
    }


def _not_cancelled(doc: dict | None) -> None:
    if doc and doc.get("cancelled"):
        raise HTTPException(409, f"Sales order {doc['sales_order_id']} was cancelled: it is not priced again")


async def _record_transaction(doc: dict, kind: str, by: str | None, request: dict | None, pricing: dict | None) -> dict:
    """Store one immutable pricing transaction (Functional Spec §16, Data Spec §20) and return its identifiers."""
    seq = doc.get("submission_count") or 1
    ids = {
        "pricingRequestId": f"PR-{doc['sales_order_id']}-{seq:02d}",
        "submissionSequence": seq,
        "previousPricingRequestId": f"PR-{doc['sales_order_id']}-{seq - 1:02d}" if seq > 1 else None,
    }
    await get_db().pricing_transactions.insert_one(
        {
            "pricing_request_id": ids["pricingRequestId"],
            "sales_order_id": doc["sales_order_id"],
            "source": doc["source"],
            "submission_sequence": seq,
            "previous_pricing_request_id": ids["previousPricingRequestId"],
            "kind": kind,
            "submitted_by": by,
            "submitted_at": utcnow(),
            "engine_version": (pricing or {}).get("engineVersion"),
            "response_status": (pricing or {}).get("responseStatus"),
            "request": request,
            "response": pricing,
        }
    )
    await get_db().sales_orders.update_one(
        {"_id": doc["_id"]}, {"$set": {"latest_pricing_request_id": ids["pricingRequestId"]}}
    )
    return ids


async def receive(order: NormalisedSalesOrder, principal: dict, raw: dict | None = None) -> dict:
    """Price and store the order (one record per source and sales order id; a resend replaces it).

    Returns the pricing result: what the sending system uses to update the sales-order prices.
    """
    so = order.sales_order
    db = get_db()
    _not_cancelled(
        await db.sales_orders.find_one(
            {"source": so.source, "sales_order_id": so.sales_order_id}, {"cancelled": 1, "sales_order_id": 1}
        )
    )
    pricing = await order_pricing.price(order)
    now = utcnow()
    record = {
        "status": so.status,
        "customer_id": order.customer.customer_id,
        "customer_name": order.customer.customer_name,
        "customer_group": order.customer.customer_group,
        "channel": order.customer.channel,
        "banner": order.customer.banner,
        "order_date": to_dt(order.order_date or today()),
        "currency": app_settings.currency(),
        "line_count": len(order.lines),
        "total": order_total(order),
        "sales_order": order.model_dump(mode="json", by_alias=True),
        "last_received_at": now,
        "last_received_by": principal.get("name"),
        **_pricing_fields(pricing),
    }
    if raw is not None:
        record["raw_request"] = raw
    doc = await db.sales_orders.find_one_and_update(
        {"source": so.source, "sales_order_id": so.sales_order_id},
        {
            "$set": record,
            "$setOnInsert": {"first_received_at": now},
            "$inc": {"receive_count": 1, "submission_count": 1},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    request = {"normalised": record["sales_order"], **({"raw": raw} if raw is not None else {})}
    ids = await _record_transaction(doc, "RECEIVED", principal.get("name"), request, pricing)
    return {**ids, **pricing, "source": so.source, "receivedAt": now.isoformat()}


async def evaluate_stored(sales_order_id: str, source: str | None, by: str | None = None) -> dict:
    """Price a stored order again with the campaigns approved now (the order itself is unchanged)."""
    query = {"sales_order_id": sales_order_id, **({"source": source} if source else {})}
    doc = await get_db().sales_orders.find_one(query, sort=[("last_received_at", -1)])
    if not doc:
        raise HTTPException(404, f"Sales order {sales_order_id} not found")
    _not_cancelled(doc)
    order = NormalisedSalesOrder.model_validate(doc["sales_order"])
    pricing = await order_pricing.price(order)
    doc = await get_db().sales_orders.find_one_and_update(
        {"_id": doc["_id"]},
        {"$set": _pricing_fields(pricing), "$inc": {"submission_count": 1}},
        return_document=ReturnDocument.AFTER,
    )
    ids = await _record_transaction(doc, "REPRICED", by, {"normalised": doc["sales_order"]}, pricing)
    return {**ids, **pricing, "source": doc["source"]}


async def cancel(body: CancellationIn, principal: dict) -> dict:
    """Mark the order cancelled (Functional Spec §12): no repricing, no history deleted."""
    query = {
        "sales_order_id": body.sales_order_id,
        **({"source": body.source_system.upper()} if body.source_system else {}),
    }
    db = get_db()
    doc = await db.sales_orders.find_one(query, sort=[("last_received_at", -1)])
    if not doc:
        raise HTTPException(404, f"Sales order {body.sales_order_id} not found")
    if not doc.get("cancelled"):
        at = body.cancelled_at or utcnow()
        await db.sales_orders.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "cancelled": True,
                    "cancelled_at": at,
                    "cancelled_by": principal.get("name"),
                    "pricing_status": "CANCELLED",
                }
            },
        )
        await db.pricing_transactions.insert_one(
            {
                "pricing_request_id": None,
                "sales_order_id": doc["sales_order_id"],
                "source": doc["source"],
                "submission_sequence": doc.get("submission_count") or 0,
                "previous_pricing_request_id": body.latest_pricing_request_id or doc.get("latest_pricing_request_id"),
                "kind": "CANCELLED",
                "submitted_by": principal.get("name"),
                "submitted_at": utcnow(),
                "cancelled_at": at,
                "request": body.model_dump(mode="json", by_alias=True),
                "response": None,
            }
        )
        await write_audit(
            "SALES_ORDER",
            doc["sales_order_id"],
            "CANCEL",
            principal.get("name") or "?",
            details=f"Source {doc['source']}",
        )
    doc = await db.sales_orders.find_one({"_id": doc["_id"]}, {"cancelled_at": 1, "sales_order_id": 1, "source": 1})
    return {
        "salesOrderId": doc["sales_order_id"],
        "source": doc["source"],
        "pricingStatus": "CANCELLED",
        "cancelledAt": serialize(doc)["cancelled_at"],
    }


async def pricing_history(sales_order_id: str, source: str | None = None) -> list[dict]:
    """Every pricing transaction of the order, newest first (immutable; never replaced by a later one)."""
    query = {"sales_order_id": sales_order_id, **({"source": source} if source else {})}
    cursor = get_db().pricing_transactions.find(query).sort([("submitted_at", -1)])
    return [serialize(d) async for d in cursor]


_LIST_FIELDS = {
    "sales_order_id": 1,
    "source": 1,
    "status": 1,
    "customer_id": 1,
    "channel": 1,
    "banner": 1,
    "order_date": 1,
    "currency": 1,
    "line_count": 1,
    "total": 1,
    "customer_name": 1,
    "customer_group": 1,
    "pricing_status": 1,
    "final_total": 1,
    "savings": 1,
    "promotion_line_count": 1,
    "last_priced_at": 1,
    "receive_count": 1,
    "first_received_at": 1,
    "last_received_at": 1,
    "last_received_by": 1,
    "cancelled": 1,
    "cancelled_at": 1,
    "submission_count": 1,
    "latest_pricing_request_id": 1,
}


async def list_sales_orders(q, status, channel, date_from, date_to, page: int, page_size: int) -> dict:
    db = get_db()
    conds = []
    if q:
        rx = search_regex(q)
        conds.append({"$or": [{"sales_order_id": rx}, {"customer_id": rx}, {"customer_name": rx}]})
    if status:
        conds.append({"status": status})
    if channel:
        conds.append({"channel": channel})
    if date_from or date_to:
        rng = {}
        if date_from:
            rng["$gte"] = to_dt(date_from)
        if date_to:
            rng["$lte"] = to_dt(date_to)
        conds.append({"order_date": rng})
    query = {"$and": conds} if conds else {}
    total = await db.sales_orders.count_documents(query)
    cursor = (
        db.sales_orders.find(query, _LIST_FIELDS)
        .sort([("last_received_at", -1), ("sales_order_id", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    items = [serialize(d) async for d in cursor]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "channels": sorted(c for c in await db.sales_orders.distinct("channel") if c),
        "statuses": sorted(s for s in await db.sales_orders.distinct("status") if s),
    }


async def get_sales_order(sales_order_id: str, source: str | None = None) -> dict:
    query = {"sales_order_id": sales_order_id, **({"source": source} if source else {})}
    doc = await get_db().sales_orders.find_one(
        query, {**_LIST_FIELDS, "sales_order": 1, "raw_request": 1, "pricing": 1}, sort=[("last_received_at", -1)]
    )
    if not doc:
        raise HTTPException(404, f"Sales order {sales_order_id} not found")
    order = NormalisedSalesOrder.model_validate(doc["sales_order"])
    out = {**serialize(doc), "lines": serialize([_line_out(ln) for ln in order.lines])}
    out["priced_request"] = await _priced_request(out)
    return out


# Lines of an order received in the normalised format, and where their price is.
_NORMALISED_PROFILE = {
    "lines": {"path": ["lines"], "fields": {"currentUnitPrice": ["currentUnitPrice", "baseUnitPrice"]}}
}


async def _priced_request(order: dict) -> dict | None:
    """The order JSON as received, with ``new_price`` below the price of every line a promotion was applied to."""
    if not order.get("pricing"):
        return None
    if order.get("raw_request") is not None:
        profile, _ = await get_profile(order["source"])
        return with_new_prices(order["raw_request"], profile, order["pricing"]["items"])
    return with_new_prices(order["sales_order"], _NORMALISED_PROFILE, order["pricing"]["items"])


# --------------------------------------------------------------------------- integration adapters
async def normalise(source: str, payload: dict) -> NormalisedSalesOrder:
    profile, _ = await get_profile(source)
    try:
        mapped = map_sales_order(payload, profile, source.upper())
    except MappingError as e:
        raise HTTPException(422, {"code": "MAPPING_ERROR", "message": str(e), "errors": e.errors}) from None
    try:
        return NormalisedSalesOrder.model_validate(mapped)
    except ValidationError as e:
        errors = [
            {"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
            for err in e.errors(include_url=False)
        ]
        raise HTTPException(
            422,
            {
                "code": "MAPPING_ERROR",
                "message": "; ".join(f"{x['field']}: {x['message']}" for x in errors),
                "errors": errors,
            },
        ) from None
