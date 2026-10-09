"""Integration / mapping layer: converts external sales-order payloads into the normalised sales order.

A *mapping profile* says, for every normalised field, which source path(s) hold it (the first path that has a
value wins). The default NetSuite profile below matches the SuiteCommerce cart payload (``cartItems``,
``params.attributes``, ``profileId`` ...). An administrator can store an adjusted profile in MongoDB
(``integration_mappings``), so a renamed NetSuite field is a configuration change, not a code change.

Paths are dot separated (``prices.base.price``). ``$key`` means "the key of a single-key wrapper object", for
payloads shaped like ``{"SO0097888": {...order...}}``.
"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException

from app.common.utils import utcnow
from app.core.database import get_db
from app.modules.audit.service import write_audit

SOURCES = ("NETSUITE",)

DEFAULT_PROFILES: dict[str, dict] = {
    "NETSUITE": {
        # Both shapes NetSuite sends are covered: fields at the top level, or inside "params".
        "order": {
            "salesOrderId": ["orderId", "salesOrderId", "tranId", "id", "params.orderId", "$key"],
            "status": ["state", "params.state", "status"],
            "isNew": ["isNew", "params.isNew"],
            "profileId": ["profileId", "params.profileId"],
            "storeIntegrationId": ["storeIntegrationId", "params.storeIntegrationId"],
            "orderDate": ["orderDate", "tranDate", "trandate", "params.orderDate", "params.attributes.trandate"],
            "couponCodes": ["couponCodes", "params.couponCodes"],
            "promotionCode": ["promotionCode", "params.promotionCode"],
            # Not in the NetSuite cart payload yet: add the paths when NetSuite sends them (Data Spec §14.1).
            "orderStatus": [],
            "pricingStatus": [],
        },
        "customer": {
            "customerId": ["profileId", "params.profileId", "customerId", "entity"],
            "channel": ["params.attributes.cseg_hogchannel"],
            "banner": ["params.attributes.custbodycustbody_hog_bnr_strn"],
            "customerName": [],
            # Not in the NetSuite cart payload: add the paths here when NetSuite sends them (Data Spec §14.2).
            "customerGroup": [],
            "marketingFlag": [],
            "priceLevelType": [],
            "baseDiscountPercentage": [],
            "accelerateEligible": [],
            "monthlyPromotionEligible": [],
            "mtsEligible": [],
            "assignedPriceListId": [],
        },
        "lines": {
            # The first path that holds a list of lines is used.
            "path": ["cartItems", "params.cartItems"],
            "fields": {
                "lineId": ["attributes.Line_ID", "position"],
                "itemCode": ["sku"],
                "itemId": ["attributes.id"],
                "itemName": ["product.name"],
                "quantity": ["quantity"],
                "remainingQuantity": ["remainingQuantity"],
                "returnedQuantity": ["returnedQuantity"],
                "currentUnitPrice": ["price"],
                "baseUnitPrice": ["prices.base.price"],
                "rrp": ["attributes.rrp"],
                "itemFlag": ["attributes.custitem_hog_itemflag"],
                # The item group campaigns use (e.g. FG-AU); by default the item flag.
                "itemGroup": ["attributes.custitem_hog_itemflag"],
                "shipperQuantity": ["attributes.custitem_hog_shipper_qty"],
                # The cart's base price list price is the item's Wholesale Price.
                "wholesalePrice": ["prices.base.price"],
                # Not in the NetSuite cart payload yet (Data Spec §14.3).
                "category": [],
                "accelerateFlag": [],
                "commodityFlag": [],
                "lineType": [],
                "excludePricingLine": [],
                "manualOverride": [],
                "existingManualRate": [],
            },
            # Every value under this path is kept as a line attribute (traceability, product attribute rules).
            "attributes": "attributes",
        },
    }
}

ORDER_FIELDS = set(DEFAULT_PROFILES["NETSUITE"]["order"])
CUSTOMER_FIELDS = set(DEFAULT_PROFILES["NETSUITE"]["customer"])
LINE_FIELDS = set(DEFAULT_PROFILES["NETSUITE"]["lines"]["fields"])
REQUIRED_LINE_FIELDS = ("lineId", "itemCode", "quantity")


class MappingError(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(e["message"] for e in errors))
        self.errors = errors


# --------------------------------------------------------------------------- profiles (MongoDB)
async def get_profile(source: str) -> tuple[dict, bool]:
    """(profile, customised) - the stored profile, else the built-in default."""
    source = source.upper()
    if source not in SOURCES:
        raise HTTPException(404, f"Unknown integration source {source}")
    doc = await get_db().integration_mappings.find_one({"_id": source})
    if doc:
        return _with_new_fields(doc["profile"], DEFAULT_PROFILES[source]), True
    return deepcopy(DEFAULT_PROFILES[source]), False


def _with_new_fields(profile: dict, default: dict) -> dict:
    """A saved profile plus any field added to the default since it was saved (so every field stays visible and
    mappable); the saved paths are never changed."""
    out = deepcopy(profile)
    for section in ("order", "customer"):
        for k, v in default[section].items():
            out.setdefault(section, {}).setdefault(k, deepcopy(v))
    for k, v in default["lines"]["fields"].items():
        out.setdefault("lines", {}).setdefault("fields", {}).setdefault(k, deepcopy(v))
    return out


def check_profile(profile: dict) -> None:
    problems = []
    for section, allowed in (("order", ORDER_FIELDS), ("customer", CUSTOMER_FIELDS)):
        block = profile.get(section)
        if not isinstance(block, dict):
            problems.append(f"'{section}' must be an object")
            continue
        for k, v in block.items():
            if k not in allowed:
                problems.append(f"{section}.{k} is not a known field")
            elif not isinstance(v, list) or not all(isinstance(p, str) for p in v):
                problems.append(f"{section}.{k} must be a list of paths")
    lines = profile.get("lines")
    if not isinstance(lines, dict) or not _paths(lines.get("path")):
        problems.append("'lines.path' must name the array of order lines (a path or a list of paths)")
    else:
        fields = lines.get("fields")
        if not isinstance(fields, dict):
            problems.append("'lines.fields' must be an object")
        else:
            for k, v in fields.items():
                if k not in LINE_FIELDS:
                    problems.append(f"lines.fields.{k} is not a known field")
                elif not isinstance(v, list) or not all(isinstance(p, str) for p in v):
                    problems.append(f"lines.fields.{k} must be a list of paths")
            for k in REQUIRED_LINE_FIELDS:
                if not fields.get(k):
                    problems.append(f"lines.fields.{k} needs at least one path")
    if not (profile.get("customer") or {}).get("customerId"):
        problems.append("customer.customerId needs at least one path")
    if not (profile.get("order") or {}).get("salesOrderId"):
        problems.append("order.salesOrderId needs at least one path")
    if problems:
        raise HTTPException(422, "Invalid mapping profile: " + "; ".join(problems))


async def save_profile(source: str, profile: dict, username: str) -> dict:
    source = source.upper()
    before, _ = await get_profile(source)
    check_profile(profile)
    await get_db().integration_mappings.replace_one(
        {"_id": source},
        {"_id": source, "profile": profile, "updated_by": username, "updated_at": utcnow()},
        upsert=True,
    )
    await write_audit("INTEGRATION_MAPPING", source, "UPDATE", username, {"profile": before}, {"profile": profile})
    return profile


async def reset_profile(source: str, username: str) -> dict:
    source = source.upper()
    before, customised = await get_profile(source)
    if customised:
        await get_db().integration_mappings.delete_one({"_id": source})
        await write_audit(
            "INTEGRATION_MAPPING",
            source,
            "RESET",
            username,
            {"profile": before},
            details="Reset to the default profile",
        )
    return deepcopy(DEFAULT_PROFILES[source])


# --------------------------------------------------------------------------- mapping
def _get(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _first(obj: Any, paths: list[str], key: str | None) -> Any:
    for p in paths:
        v = key if p == "$key" else _get(obj, p)
        if v not in (None, ""):
            return v
    return None


def _paths(value) -> list[str]:
    """A path setting may be one path or a list of paths."""
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list) and value and all(isinstance(p, str) and p for p in value):
        return value
    return []


def _find_lines(order: dict, paths: list[str]):
    for p in paths:
        v = _get(order, p)
        if isinstance(v, list):
            return v, p
    return None, None


def _unwrap(payload: dict, lines_paths: list[str]) -> tuple[dict, str | None]:
    """Accept either the order object itself or a single-key wrapper {"SO0097888": {...}}."""
    if _find_lines(payload, lines_paths)[0] is None and len(payload) == 1:
        key, value = next(iter(payload.items()))
        if isinstance(value, dict):
            return value, str(key)
    return payload, None


def _number(v: Any) -> Any:
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int | float | Decimal):
        return v
    try:
        return Decimal(str(v).strip())
    except InvalidOperation:
        return v  # left for validation to report


def _integer(v: Any) -> Any:
    n = _number(v)
    if isinstance(n, Decimal | float) and n == int(n):
        return int(n)
    return n


def _bool(v: Any) -> Any:
    if isinstance(v, str):
        return {"true": True, "false": False}.get(v.strip().lower(), v)
    return v


def map_sales_order(payload: dict, profile: dict, source: str) -> dict:
    """Raw payload -> normalised sales-order dict (camelCase). Raises MappingError for unusable input."""
    if not isinstance(payload, dict):
        raise MappingError([{"field": "(body)", "message": "The sales order must be a JSON object"}])
    lines_cfg = profile["lines"]
    line_paths = _paths(lines_cfg["path"])
    order, key = _unwrap(payload, line_paths)
    errors: list[dict] = []

    header = {f: _first(order, paths, key) for f, paths in profile["order"].items() if f != "orderDate"}
    header["isNew"] = _bool(header.get("isNew"))
    header["couponCodes"] = [str(c) for c in (header.get("couponCodes") or []) if c not in (None, "")]
    header["source"] = source
    customer = {f: _first(order, paths, key) for f, paths in profile["customer"].items()}
    for f in ("accelerateEligible", "monthlyPromotionEligible", "mtsEligible"):
        customer[f] = _bool(customer.get(f))
    customer["baseDiscountPercentage"] = _number(customer.get("baseDiscountPercentage"))
    if not header.get("salesOrderId"):
        errors.append(
            {
                "field": "salesOrder.salesOrderId",
                "message": "Sales order id not found (" + ", ".join(profile["order"]["salesOrderId"]) + ")",
            }
        )
    if not customer.get("customerId"):
        errors.append(
            {
                "field": "customer.customerId",
                "message": "Customer id not found (" + ", ".join(profile["customer"]["customerId"]) + ")",
            }
        )

    raw_lines, _found_at = _find_lines(order, line_paths)
    if not raw_lines:
        errors.append(
            {"field": "lines", "message": f"No order lines found at {', '.join(repr(p) for p in line_paths)}"}
        )
        raw_lines = []
    lines = []
    for idx, raw in enumerate(raw_lines):
        if not isinstance(raw, dict):
            errors.append({"field": f"lines[{idx}]", "message": "Order line is not an object"})
            continue
        line = {f: _first(raw, paths, None) for f, paths in lines_cfg["fields"].items()}
        label = f"line {line.get('lineId') if line.get('lineId') is not None else idx + 1}"
        for f in REQUIRED_LINE_FIELDS:
            if line.get(f) in (None, ""):
                errors.append(
                    {
                        "field": f"lines[{idx}].{f}",
                        "message": f"{label}: {f} not found ({', '.join(lines_cfg['fields'][f])})",
                    }
                )
        for f in ("quantity", "remainingQuantity", "returnedQuantity", "shipperQuantity"):
            line[f] = _integer(line.get(f))
        for f in (
            "currentUnitPrice",
            "baseUnitPrice",
            "rrp",
            "wholesalePrice",
            "existingManualRate",
            "shipperQuantity",
        ):
            line[f] = _number(line.get(f))
        for f in ("accelerateFlag", "commodityFlag", "excludePricingLine", "manualOverride"):
            line[f] = _bool(line.get(f))
        attrs = _get(raw, lines_cfg["attributes"]) if lines_cfg.get("attributes") else None
        line["attributes"] = {k: v for k, v in (attrs or {}).items()} if isinstance(attrs, dict) else {}
        lines.append({k: v for k, v in line.items() if v is not None or k == "attributes"})
    if errors:
        raise MappingError(errors)

    normalised = {
        "salesOrder": {k: v for k, v in header.items() if v is not None},
        "customer": {k: v for k, v in customer.items() if v is not None},
        "lines": lines,
    }
    order_date = _first(order, profile["order"].get("orderDate", []), key)
    if order_date:
        normalised["orderDate"] = str(order_date)[:10]
    return normalised


# --------------------------------------------------------------------------- the order with its promotion prices
NEW_PRICE = "new_price"


def _insert_after(line: dict, paths: list[str], key: str, value: Any) -> None:
    """Put ``key`` right after the line's price (the first of ``paths`` it has), keeping every other key in place."""
    for path in paths:
        *parent_path, last = path.split(".")
        parent = _get(line, ".".join(parent_path)) if parent_path else line
        if isinstance(parent, dict) and last in parent:
            entries = list(parent.items())
            position = [k for k, _ in entries].index(last) + 1
            entries.insert(position, (key, value))
            parent.clear()
            parent.update(entries)
            return
    line[key] = value


def with_new_prices(payload: dict, profile: dict, items: list[dict]) -> dict:
    """A copy of the order exactly as received, with ``new_price`` (the price after the promotion) placed right
    below ``price`` on every line a promotion was applied to. Lines without a promotion are unchanged."""
    out = deepcopy(payload)
    line_paths = _paths(profile["lines"]["path"])
    order, _ = _unwrap(out, line_paths)
    raw_lines, _ = _find_lines(order, line_paths)
    price_paths = [
        *profile["lines"]["fields"].get("currentUnitPrice", []),
        *profile["lines"]["fields"].get("baseUnitPrice", []),
    ]
    for raw, item in zip(raw_lines or [], items, strict=False):
        if isinstance(raw, dict) and item.get("promotionApplied"):
            _insert_after(raw, price_paths, NEW_PRICE, item["finalPrice"])
    return out
