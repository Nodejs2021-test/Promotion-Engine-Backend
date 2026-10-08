"""Sales orders: normalised sales order -> pricing (campaign rules) -> stored record -> list / details.

The normalised order (``modules/sales_orders/schema.py``) comes from ``POST /sales-orders``, ``POST /pricing/evaluate``
or the NetSuite field mapping (``integrations/netsuite/mapping.py``). Every order is priced against the approved campaigns,
stored as received together with its pricing result, and the pricing result is returned to the sender.
"""

from decimal import Decimal

from fastapi import HTTPException
from pydantic import ValidationError

from app.common.utils import search_regex, serialize, to_dt, today, utcnow
from app.core import app_settings
from app.core.database import get_db
from app.integrations.netsuite.mapping import MappingError, get_profile, map_sales_order, with_new_prices
from app.modules.pricing import service as order_pricing
from app.modules.sales_orders.schema import NormalisedSalesOrder, SalesOrderLine


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
        "last_priced_at": utcnow(),
    }


async def receive(order: NormalisedSalesOrder, principal: dict, raw: dict | None = None) -> dict:
    """Price and store the order (one record per source and sales order id; a resend replaces it).

    Returns the pricing result: what the sending system uses to update the sales-order prices.
    """
    so = order.sales_order
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
    await get_db().sales_orders.update_one(
        {"source": so.source, "sales_order_id": so.sales_order_id},
        {"$set": record, "$setOnInsert": {"first_received_at": now}, "$inc": {"receive_count": 1}},
        upsert=True,
    )
    return {**pricing, "source": so.source, "receivedAt": now.isoformat()}


async def evaluate_stored(sales_order_id: str, source: str | None) -> dict:
    """Price a stored order again with the campaigns approved now (the order itself is unchanged)."""
    query = {"sales_order_id": sales_order_id, **({"source": source} if source else {})}
    doc = await get_db().sales_orders.find_one(query, sort=[("last_received_at", -1)])
    if not doc:
        raise HTTPException(404, f"Sales order {sales_order_id} not found")
    order = NormalisedSalesOrder.model_validate(doc["sales_order"])
    pricing = await order_pricing.price(order)
    await get_db().sales_orders.update_one({"_id": doc["_id"]}, {"$set": _pricing_fields(pricing)})
    return {**pricing, "source": doc["source"]}


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
