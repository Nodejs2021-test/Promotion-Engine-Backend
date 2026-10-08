"""Candidate unit rates (Functional Spec §5): every source produces a complete rate, percentages never stack.

Rates are calculated and compared at full precision; only the winning rate is rounded half-up to three decimals.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.modules.pricing.model import Action, OrderLine, Rule, Tier

HUNDRED = Decimal(100)
RATE = Decimal("0.001")
CENT = Decimal("0.01")


def round_rate(v: Decimal) -> Decimal:
    """Half-up to three decimals (Functional Spec §5.5)."""
    return v.quantize(RATE, rounding=ROUND_HALF_UP)


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Rate:
    unit_rate: Decimal  # unrounded
    method: str  # PERCENTAGE, FIXED_UNIT_RATE, WHOLESALE, RRP
    discount_percentage: Decimal | None  # 35 = 35 %; None for fixed rates


def percentage_rate(reference: Decimal, pct: Decimal) -> Decimal:
    return reference * (HUNDRED - pct) / HUNDRED


def action_rate(action: Action, reference: Decimal) -> Rate:
    """A rule's single outcome (rules without tiers)."""
    v = Decimal(action.value)
    if action.action_type == "PERCENTAGE":
        return Rate(percentage_rate(reference, v), "PERCENTAGE", v)
    if action.action_type == "FIXED_DISCOUNT":
        return Rate(max(reference - v, Decimal(0)), "FIXED_UNIT_RATE", None)
    return Rate(v, "FIXED_UNIT_RATE", None)  # PROMOTIONAL_PRICE / FIXED_UNIT_PRICE


def tier_rate(rule: Rule, tier: Tier, line: OrderLine, reference: Decimal) -> Rate:
    """A tier outcome, with the rule's item rate override for this tier (Data Spec §8) when one applies."""
    override = next(
        (
            o
            for o in rule.rate_overrides
            if line.item_id and o.item_id == line.item_id and o.tier_position == tier.position
        ),
        None,
    )
    if override:
        return Rate(
            percentage_rate(reference, override.discount_percentage), "PERCENTAGE", override.discount_percentage
        )
    if tier.fixed_unit_rate is not None:
        return Rate(tier.fixed_unit_rate, "FIXED_UNIT_RATE", None)
    return Rate(percentage_rate(reference, tier.discount_percentage), "PERCENTAGE", tier.discount_percentage)


def _num(v: Decimal) -> str:
    return format(Decimal(v).normalize(), "f")


def describe_action(action: Action, currency: str = "") -> str:
    v = _num(action.value)
    money_text = f"{currency} {v}".strip()
    return {
        "PERCENTAGE": f"{v}% discount",
        "FIXED_DISCOUNT": f"{money_text} off per unit",
        "PROMOTIONAL_PRICE": f"Promotional price {money_text}",
        "FIXED_UNIT_PRICE": f"Fixed unit price {money_text}",
    }[action.action_type]


def describe_tier(t: Tier, currency: str = "") -> str:
    upto = f" to < {_num(t.max_quantity)}" if t.max_quantity is not None else "+"
    outcome = (
        f"fixed rate {currency} {_num(t.fixed_unit_rate)}".replace("  ", " ")
        if t.fixed_unit_rate is not None
        else f"{_num(t.discount_percentage)}% discount"
    )
    return f"Tier {t.position}: qty {_num(t.min_quantity)}{upto} → {outcome}"


def describe_outcome(rule: Rule, currency: str = "") -> str:
    if rule.family == "CAP":
        return f"Maximum discount {_num(rule.max_discount_percentage or 0)}%"
    if rule.bonus:
        b = rule.bonus
        each = "each" if b.repeatable else "once"
        return f"Buy {_num(b.buy_quantity)} get {_num(b.bonus_quantity)} free ({each})"
    if rule.family == "PRICE_LIST":
        return f"Fixed item rates ({sum(1 for i in rule.items if i.rate is not None)} items)"
    if rule.tiers:
        return "; ".join(describe_tier(t, currency) for t in sorted(rule.tiers, key=lambda t: t.position))
    return describe_action(rule.action, currency) if rule.action else "—"
