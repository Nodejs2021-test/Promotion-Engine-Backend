"""Rule validity, audience, programme eligibility and item scope.

Each function appends the checks it makes to the rule outcome, in order, and stops at the first one that fails. An
attribute the order must supply but does not is recorded in ``outcome.missing`` (PRICING_CONTEXT_INCOMPLETE).
"""

from datetime import date
from decimal import Decimal

from app.modules.pricing.conditions import combine, describe, field_value, is_missing, matches, to_bool
from app.modules.pricing.model import (
    CUSTOMER_FIELDS,
    GENERAL_RANK,
    PRODUCT_FIELDS,
    PROGRAMME_FIELD,
    PROGRAMMES,
    SPECIFICITY_RANK,
    Campaign,
    Check,
    Order,
    OrderLine,
    Rule,
    RuleItem,
    RuleOutcome,
)


def _period(start: date | None, end: date | None) -> str:
    return f"{start or '…'} to {end or 'open'}"


def date_checks(o: RuleOutcome, on: date) -> bool:
    campaign, rule = o.campaign, o.rule
    if not (campaign.approved and campaign.enabled):
        o.checks.append(Check("Campaign approved and enabled", False))
        return False
    if not campaign.start_date <= on <= campaign.end_date:
        o.checks.append(Check(f"Campaign active on {on} ({_period(campaign.start_date, campaign.end_date)})", False))
        return False
    o.checks.append(Check("Campaign Active", True))
    if not rule.active:
        o.checks.append(Check("Rule active", False))
        return False
    if (rule.start_date and on < rule.start_date) or (rule.end_date and on > rule.end_date):
        o.checks.append(Check(f"Rule valid on {on} ({_period(rule.start_date, rule.end_date)})", False))
        return False
    return True


def _list_check(label: str, allowed: frozenset[str], actual) -> Check:
    ok = actual is not None and str(actual).strip().casefold() in {a.casefold() for a in allowed}
    shown = ", ".join(sorted(allowed))
    return Check(f"{label} {'=' if len(allowed) == 1 else 'is one of'} {shown}", ok)


def _conditions(o: RuleOutcome, fields, order: Order, line: OrderLine, value: Decimal) -> bool:
    for c in combine(o.rule.conditions):
        if c.field not in fields:
            continue
        actual = field_value(c.field, order, line, value)
        if is_missing(c.field, actual):
            o.missing.append(c.field)
            return False
        check = Check(describe(c), matches(c, actual))
        if check.passed and any(x.text == check.text for x in o.checks):
            continue  # the campaign already checked exactly this
        o.checks.append(check)
        if not check.passed:
            return False
    return True


def audience_checks(o: RuleOutcome, order: Order, line: OrderLine, value: Decimal) -> bool:
    """The campaign's customer limits, then the rule's audience conditions (Customer, Group, Banner, Flag, Channel)."""
    c, campaign = order.customer, o.campaign
    for label, allowed, actual in (
        ("Customer", campaign.customer_ids, c.customer_id),
        ("Customer Group", campaign.customer_groups, c.customer_group),
        ("Channel", campaign.channels, c.channel),
    ):
        if allowed:
            o.checks.append(_list_check(label, allowed, actual))
            if not o.checks[-1].passed:
                return False
    return _conditions(o, CUSTOMER_FIELDS, order, line, value)


def programme_check(o: RuleOutcome, order: Order) -> bool:
    """Programme participation (Item Scope Spec §5): eligibility permits evaluation, it never creates a result."""
    codes = [o.rule.programme_code] if o.rule.programme_code else []
    if o.rule.family == "MONTHLY_PROMO" and "MONTHLY_PROMO" not in codes:
        codes.append("MONTHLY_PROMO")
    for code in codes:
        attr = PROGRAMME_FIELD[code]
        eligible = to_bool(getattr(order.customer, attr))
        if eligible is None:
            o.missing.append(attr)
            return False
        o.checks.append(Check(f"Customer is {PROGRAMMES[code]} eligible", eligible))
        if not eligible:
            return False
    return True


def item_in_scope(items: tuple[RuleItem, ...], line: OrderLine) -> tuple[bool | None, RuleItem | None]:
    """Item Internal ID (else Item Code) against the rule's directly selected items.

    Returns (in scope, the selected item); in scope is None when the rule selects by Item Internal ID but the line
    carries none."""
    active = [i for i in items if i.active]
    if not active:
        return True, None

    def same(i: RuleItem) -> bool | None:
        if i.item_id:
            return None if not line.item_id else str(i.item_id).strip() == str(line.item_id).strip()
        return bool(i.item_code) and str(i.item_code).strip().casefold() == line.sku.strip().casefold()

    for i in active:
        if not i.include and same(i):
            return False, i
    included = [i for i in active if i.include]
    if not included:
        return True, None
    results = [(same(i), i) for i in included]
    hit = next((i for ok, i in results if ok), None)
    if hit:
        return True, hit
    return (None, None) if any(ok is None for ok, _ in results) else (False, None)


def item_checks(o: RuleOutcome, order: Order, line: OrderLine, value: Decimal) -> bool:
    """The campaign's item limits, the rule's selected items, then the rule's item attribute conditions."""
    campaign, rule = o.campaign, o.rule
    for label, allowed, actual in (
        ("SKU", campaign.skus, line.sku),
        ("Item Group", campaign.item_groups, line.item_group),
    ):
        if allowed:
            o.checks.append(_list_check(label, allowed, actual))
            if not o.checks[-1].passed:
                return False
    if rule.items:
        ok, _ = item_in_scope(rule.items, line)
        if ok is None:
            o.missing.append("item_id")
            return False
        o.checks.append(Check(f"Item {line.item_id or line.sku} selected in the rule", ok))
        if not ok:
            return False
    return _conditions(o, PRODUCT_FIELDS, order, line, value)


def specificity_rank(campaign: Campaign, rule: Rule) -> int:
    """Functional Spec §15: Customer 1, Banner 2, Marketing Flag / group 3, Channel 4, general rule 5."""
    restricted = {
        f
        for f, values in (
            ("customer_id", campaign.customer_ids),
            ("customer_group", campaign.customer_groups),
            ("channel", campaign.channels),
        )
        if values
    }
    restricted |= {c.field for c in rule.conditions if c.field in SPECIFICITY_RANK and c.operator in ("equals", "in")}
    return min((SPECIFICITY_RANK[f] for f in restricted), default=GENERAL_RANK)
