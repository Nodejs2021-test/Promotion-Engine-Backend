"""Quantity bases (Functional Spec §8, Data Spec §7) and tier selection.

Only chargeable, non-excluded, positive lines ever contribute to a quantity.
"""

from decimal import Decimal

from app.modules.pricing.eligibility import item_checks, item_in_scope
from app.modules.pricing.model import Check, Order, OrderLine, RuleOutcome, Tier


def _in_pool(o: RuleOutcome, line: OrderLine) -> bool:
    pool = [
        i
        for i in o.rule.items
        if i.include and i.active and (not o.rule.mixed_pool_id or i.mixed_pool_id == o.rule.mixed_pool_id)
    ]
    return bool(pool) and bool(item_in_scope(tuple(pool), line)[0])


def _in_scope(o: RuleOutcome, order: Order, line: OrderLine, value: Decimal) -> bool:
    probe = RuleOutcome(o.campaign, o.rule)
    return item_checks(probe, order, line, value) and probe.matched


def qualifying_quantity(o: RuleOutcome, order: Order, line: OrderLine, value: Decimal) -> Decimal | None:
    """The quantity the rule's tiers are compared with, or None when the line does not qualify."""
    rule, basis = o.rule, o.rule.quantity_basis
    chargeable = [ln for ln in order.lines if ln.chargeable]
    if basis == "DIRECT_ITEM_GROUP_QUANTITY":
        if not _in_pool(o, line):
            o.checks.append(Check(f"Item in mixed pool {rule.mixed_pool_id or ''}".strip(), False))
            return None
        qty = Decimal(sum(ln.quantity for ln in chargeable if _in_pool(o, ln)))
        o.checks.append(Check(f"Mixed pool quantity {qty}", True))
        return qty
    if basis == "ORDER_QUALIFYING_QUANTITY":
        qty = Decimal(sum(ln.quantity for ln in chargeable if _in_scope(o, order, ln, value)))
        o.checks.append(Check(f"Order qualifying quantity {qty}", True))
        return qty
    if basis == "SHIPPER_QUANTITY":
        if line.shipper_quantity is None or line.shipper_quantity <= 0:
            o.error = {
                "code": "INVALID_SHIPPER_QUANTITY",
                "message": f"Line {line.line_id}: Shipper Quantity is missing, zero or negative",
            }
            return None
        need = line.shipper_quantity * rule.required_shipper_multiple
        ok = Decimal(line.quantity) >= need
        o.checks.append(
            Check(f"Quantity {line.quantity} >= shipper {line.shipper_quantity} x {rule.required_shipper_multiple}", ok)
        )
        return Decimal(line.quantity) if ok else None
    if basis == "ANY_QUANTITY":
        ok = line.quantity >= 1
        o.checks.append(Check("At least one chargeable unit", ok))
        return Decimal(line.quantity) if ok else None
    return Decimal(line.quantity)  # LINE_QUANTITY


def select_tier(o: RuleOutcome, qty: Decimal) -> Tier | None:
    """The highest tier whose minimum (inclusive) is reached and maximum (exclusive) is not."""
    tiers = sorted(o.rule.tiers, key=lambda t: t.min_quantity, reverse=True)
    if o.rule.quantity_basis == "SHIPPER_QUANTITY":
        tier = min(o.rule.tiers, key=lambda t: t.position, default=None)
    else:
        tier = next(
            (t for t in tiers if t.min_quantity <= qty and (t.max_quantity is None or qty < t.max_quantity)), None
        )
    label = f"Tier {tier.position} (from {tier.min_quantity})" if tier else "a quantity tier"
    o.checks.append(Check(f"Quantity {qty} reaches {label}", tier is not None))
    return tier
