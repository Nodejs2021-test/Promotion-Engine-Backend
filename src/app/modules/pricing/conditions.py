"""Rule conditions: read a field from the order / line and compare it with the condition's value."""

from decimal import Decimal

from app.modules.pricing.model import (
    BOOLEAN_FIELDS,
    FIELD_LABELS,
    OPERATOR_SYMBOLS,
    Condition,
    Order,
    OrderLine,
)

# Attributes the Sales Order must supply (Data Spec §14.2 / §14.3). When a rule needs one and the order does not carry
# it, the result is PRICING_CONTEXT_INCOMPLETE, never a guessed value or a silent "no match".
REQUIRED_FIELDS = frozenset(
    {"channel", "accelerate_eligible", "monthly_promotion_eligible", "mts_eligible", "item_id", "accelerate_flag"}
)


def _norm(v) -> str:
    return str(v).strip().casefold() if v is not None else ""


def to_bool(v) -> bool | None:
    if v is None or isinstance(v, bool):
        return v
    s = _norm(v)
    return True if s in ("true", "yes", "1", "y") else False if s in ("false", "no", "0", "n") else None


def field_value(field: str, order: Order, line: OrderLine, order_value: Decimal):
    c = order.customer
    return {
        "customer_id": c.customer_id,
        "customer_group": c.customer_group,
        "channel": c.channel,
        "banner": c.banner,
        "marketing_flag": c.marketing_flag,
        "accelerate_eligible": c.accelerate_eligible,
        "monthly_promotion_eligible": c.monthly_promotion_eligible,
        "mts_eligible": c.mts_eligible,
        "item_id": line.item_id,
        "sku": line.sku,
        "product_name": line.product_name,
        "item_group": line.item_group,
        "item_flag": line.item_flag,
        "category": line.category,
        "accelerate_flag": line.accelerate_flag,
        "commodity_flag": line.commodity_flag,
        "shipper_quantity": line.shipper_quantity,
        "quantity": line.quantity,
        "order_value": order_value,
    }[field]


def is_missing(field: str, value) -> bool:
    return field in REQUIRED_FIELDS and (value is None or value == "")


def _fmt(value) -> str:
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def describe(c: Condition) -> str:
    """'Customer Group = Pharmacy', 'Quantity >= 6', 'Item Group is one of FG-AU, FG-NZ'."""
    return f"{FIELD_LABELS[c.field]} {OPERATOR_SYMBOLS[c.operator]} {_fmt(c.value)}"


def matches(c: Condition, actual) -> bool:
    if c.field in BOOLEAN_FIELDS:
        a = to_bool(actual)
        return a is not None and a == to_bool(c.value)
    if c.operator in ("gte", "lte"):
        if actual is None:
            return False
        a, v = Decimal(str(actual)), Decimal(str(c.value))
        return a >= v if c.operator == "gte" else a <= v
    if c.operator in ("in", "not_in"):
        found = _norm(actual) in {_norm(v) for v in c.value}
        return found if c.operator == "in" else not found
    same = _norm(actual) == _norm(c.value)
    return same if c.operator == "equals" else not same


def combine(conditions) -> list[Condition]:
    """Several conditions on the same text field are alternatives: "Product = A" and "Product = B" mean the line
    may be product A or B, so they are checked as "Product is one of A, B" ("is not" ones as "is not one of").
    Number and Yes / No conditions are left as they are: each must hold."""
    groups: dict[tuple[str, bool], list[Condition]] = {}
    for c in conditions:
        if c.operator not in ("gte", "lte") and c.field not in BOOLEAN_FIELDS:
            groups.setdefault((c.field, c.operator in ("equals", "in")), []).append(c)
    out: list[Condition] = []
    for c in conditions:
        key = (c.field, c.operator in ("equals", "in"))
        if c.operator in ("gte", "lte") or c.field in BOOLEAN_FIELDS:
            out.append(c)
        elif key in groups:
            group = groups.pop(key)
            if len(group) == 1:
                out.append(c)
            else:
                values = list(
                    dict.fromkeys(v for g in group for v in (g.value if isinstance(g.value, list) else [g.value]))
                )
                out.append(Condition(c.field, "in" if key[1] else "not_in", values))
    return out


def min_quantity(conditions) -> int:
    """The quantity threshold of a rule (its 'quantity >=' value; 0 when it has none)."""
    return max(
        (int(Decimal(str(c.value))) for c in conditions if c.field == "quantity" and c.operator == "gte"), default=0
    )
