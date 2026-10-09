"""evaluate_sales_order(): prices every chargeable line of a sales order (Functional Spec §14).

    validate the order (submission status, controlled values, duplicate chargeable item codes)
    bypass excluded lines; preserve manual overrides
    exclusive Price List customer → the Price List item rate, nothing else
    otherwise: base candidate; one candidate per family (Everyday, Monthly Promotion, Promotion Code, Exception) —
    the most specific matching rule of that family; apply the item cap or approved bypass; the lowest rate wins
    (an EXCLUSIVE rule wins outright); round the winning rate half-up to three decimals; same-item bonus stock.

Pure: no database, no clock. The same input always gives the same result.
"""

from dataclasses import replace
from decimal import Decimal

from app.modules.pricing import discount
from app.modules.pricing.conditions import describe, field_value, matches
from app.modules.pricing.eligibility import (
    audience_checks,
    date_checks,
    item_checks,
    item_in_scope,
    programme_check,
)
from app.modules.pricing.model import (
    CHARGEABLE,
    ENGINE_VERSION,
    LINE_TYPES,
    MONETARY_FAMILIES,
    ORDER_FIELDS,
    PRICE_LEVEL_TYPES,
    Campaign,
    Check,
    EligibilityRecord,
    Order,
    OrderLine,
    Rule,
    RuleOutcome,
    Tier,
)
from app.modules.pricing.priority import family_rules, sort_key
from app.modules.pricing.quantity import qualifying_quantity, select_tier

NO_PROMOTION = "No eligible promotion found"
TIE_ORDER = {f: i for i, f in enumerate((*MONETARY_FAMILIES, "BASE"))}
ATTRIBUTE_NAMES = {
    "channel": "customer.channel",
    "accelerate_eligible": "customer.accelerateEligible",
    "monthly_promotion_eligible": "customer.monthlyPromotionEligible",
    "mts_eligible": "customer.mtsEligible",
    "item_id": "line.itemInternalId",
    "accelerate_flag": "line.accelerateFlag",
}


def _err(code: str, message: str, **extra) -> dict:
    return {"code": code, "message": message, **extra}


def _norm(v: str | None) -> str:
    return (v or "").strip().upper().replace(" ", "_")


def order_value(order: Order) -> Decimal:
    """Quantity × reference price over the chargeable lines: used by 'Order Value >=' conditions."""
    total = Decimal(0)
    for ln in order.lines:
        price = reference_price(ln)
        if ln.chargeable and price is not None:
            total += price * ln.quantity
    return total


def reference_price(line: OrderLine) -> Decimal | None:
    """The price rule percentages apply to: Wholesale Price (else the base / order price the order carries)."""
    for p in (line.wholesale_price, line.base_price, line.current_price):
        if p is not None:
            return p
    return None


# --------------------------------------------------------------------------- order validation (§19)
def validate_order(order: Order, controlled: dict[str, list[str]]) -> list[dict]:
    errors = []
    if order.order_status or order.pricing_status:
        if _norm(order.order_status) != "PENDING_FULFILMENT" or _norm(order.pricing_status) != "READY_TO_SEND":
            errors.append(
                _err(
                    "ORDER_NOT_ELIGIBLE_FOR_SUBMISSION",
                    f"Order Status '{order.order_status}' / Pricing Status '{order.pricing_status}': only Pending "
                    "Fulfilment orders that are Ready to Send are priced",
                )
            )
    c = order.customer
    for attr, value in (("channel", c.channel), ("banner", c.banner), ("marketing_flag", c.marketing_flag)):
        allowed = {v.casefold() for v in controlled.get(attr) or []}
        if allowed and value and value.casefold() not in allowed:
            errors.append(
                _err("INVALID_CONTROLLED_ATTRIBUTE", f"{attr} '{value}' is not a controlled value", attribute=attr)
            )
    if c.price_level_type and c.price_level_type not in PRICE_LEVEL_TYPES:
        errors.append(
            _err(
                "INVALID_CONTROLLED_ATTRIBUTE",
                f"Price Level Type '{c.price_level_type}' is invalid",
                attribute="priceLevelType",
            )
        )
    for ln in order.lines:
        if ln.line_type not in LINE_TYPES:
            errors.append(
                _err(
                    "INVALID_CONTROLLED_ATTRIBUTE",
                    f"Line {ln.line_id}: Line Type '{ln.line_type}' is invalid",
                    attribute="lineType",
                )
            )
    seen: dict[str, str] = {}
    for ln in order.lines:
        if ln.line_type == CHARGEABLE and not ln.exclude_pricing_line:
            key = ln.sku.strip().casefold()
            if key in seen:
                errors.append(
                    _err(
                        "DUPLICATE_CHARGEABLE_ITEM_CODE",
                        f"Item Code {ln.sku} is on chargeable lines {seen[key]} and {ln.line_id}",
                    )
                )
            seen.setdefault(key, ln.line_id)
    return errors


# --------------------------------------------------------------------------- rule evaluation
def evaluate_rule(campaign: Campaign, rule: Rule, order: Order, line: OrderLine, value: Decimal) -> RuleOutcome:
    """Date → promotion code → programme → audience → items → order conditions → quantity basis → tier."""
    o = RuleOutcome(campaign, rule)
    if not date_checks(o, order.order_date):
        return o
    if rule.family == "PROMOTION_CODE":
        ok = (
            bool(order.promotion_code)
            and order.promotion_code.strip().casefold() == (rule.promotion_code or "").casefold()
        )
        o.checks.append(Check(f"Promotion code {rule.promotion_code}", ok))
        if not ok:
            return o
        used_total, used_customer = next(((t, c) for rid, t, c in order.code_usage if rid == rule.rule_id), (0, 0))
        for limit, used, label in (
            (rule.max_uses_total, used_total, "in total"),
            (rule.max_uses_per_customer, used_customer, "by this customer"),
        ):
            if limit:
                o.checks.append(Check(f"Code used {used} of {limit} times {label}", used < limit))
                if used >= limit:
                    return o
    if (
        not programme_check(o, order)
        or not audience_checks(o, order, line, value)
        or not item_checks(o, order, line, value)
    ):
        return o
    for c in rule.conditions:
        if c.field in ORDER_FIELDS:
            o.checks.append(Check(describe(c), matches(c, field_value(c.field, order, line, value))))
            if not o.checks[-1].passed:
                return o
    qty = qualifying_quantity(o, order, line, value)
    if qty is None:
        return o
    o.qualifying_quantity = qty
    if rule.tiers:
        o.tier = select_tier(o, qty)
    return o


def _trace(position: int, o: RuleOutcome) -> dict:
    failed = next((c.text for c in o.checks if not c.passed), None)
    if o.missing:
        failed = f"Missing {', '.join(ATTRIBUTE_NAMES.get(m, m) for m in o.missing)}"
    if o.error:
        failed = o.error["message"]
    return {
        "position": position,
        "family": o.rule.family,
        "campaignId": o.campaign.campaign_id,
        "campaignName": o.campaign.name,
        "ruleId": o.rule.rule_id,
        "ruleName": o.rule.name,
        "ruleVersion": o.rule.version,
        "result": "MATCHED" if o.matched else "NOT_MATCHED",
        "failedCheck": failed,
    }


def _cap_for(campaigns: list[Campaign], order: Order, line: OrderLine, value: Decimal) -> tuple[Rule, Decimal] | None:
    """The applicable item discount cap: the most specific, then highest-priority matching CAP rule."""
    for campaign, rule in family_rules(campaigns, "CAP"):
        if rule.max_discount_percentage is None:
            continue
        o = RuleOutcome(campaign, rule)
        if (
            date_checks(o, order.order_date)
            and audience_checks(o, order, line, value)
            and item_checks(o, order, line, value)
        ):
            if o.matched:
                return rule, rule.max_discount_percentage
    return None


def _candidate(o: RuleOutcome, line: OrderLine, reference: Decimal, cap) -> dict:
    rule = o.rule
    rate = discount.tier_rate(rule, o.tier, line, reference) if o.tier else discount.action_rate(rule.action, reference)
    _, item = item_in_scope(rule.items, line)
    if item is not None and item.role == "RATE_OVERRIDE" and item.rate is not None:
        rate = discount.Rate(item.rate, "FIXED_UNIT_RATE", None)  # this item's own rate in the rule (Item Role)
    cand = {
        "family": rule.family,
        "campaign": o.campaign,
        "rule": rule,
        "tier": o.tier,
        "outcome": o,
        "method": rate.method,
        "rate": rate.unit_rate,
        "pct": rate.discount_percentage,
        "preCapPct": None,
        "capRule": None,
        "capApplied": False,
        "capBypass": False,
    }
    return _apply_cap(cand, reference, cap, rule.cap_treatment)


def _apply_cap(cand: dict, reference: Decimal, cap, treatment: str) -> dict:
    """A cap restricts a percentage candidate; it never creates a discount (Functional Spec §7)."""
    if not cap or cand["pct"] is None or cand["pct"] <= cap[1]:
        return cand
    cap_rule, max_pct = cap
    cand["capRule"] = cap_rule
    if treatment == "APPROVED_BYPASS":
        cand["capBypass"] = True
        return cand
    cand.update(preCapPct=cand["pct"], pct=max_pct, rate=discount.percentage_rate(reference, max_pct), capApplied=True)
    return cand


def _base_candidate(order: Order, line: OrderLine) -> tuple[dict | None, dict | None, list[dict]]:
    """The customer's base pricing (Functional Spec §5.3) → (candidate, line error, warnings)."""
    c, warnings = order.customer, []
    level = c.price_level_type
    if level is None:
        price = line.current_price if line.current_price is not None else line.base_price
        if price is None:
            return (
                None,
                _err("NO_VALID_PRICING_CANDIDATE", f"Line {line.line_id}: no price on the order line"),
                warnings,
            )
        return {"family": "BASE", "method": "ORDER_PRICE", "rate": price, "pct": None}, None, warnings
    if level == "PERCENTAGE":
        if line.wholesale_price is None:
            return None, _err("WHOLESALE_PRICE_MISSING", f"Line {line.line_id}: Wholesale Price is required"), warnings
        if c.base_discount_percentage is None:
            return (
                None,
                _err(
                    "PRICING_CONTEXT_INCOMPLETE",
                    "customer.baseDiscountPercentage is required for PERCENTAGE pricing",
                    attribute="customer.baseDiscountPercentage",
                ),
                warnings,
            )
        pct = c.base_discount_percentage * 100
        return (
            {
                "family": "BASE",
                "method": "PERCENTAGE",
                "rate": discount.percentage_rate(line.wholesale_price, pct),
                "pct": pct,
            },
            None,
            warnings,
        )
    if level == "WHOLESALE":
        if line.wholesale_price is None:
            return None, _err("WHOLESALE_PRICE_MISSING", f"Line {line.line_id}: Wholesale Price is required"), warnings
        return (
            {"family": "BASE", "method": "WHOLESALE", "rate": line.wholesale_price, "pct": Decimal(0)},
            None,
            warnings,
        )
    if level == "RRP":
        if line.rrp is None:
            return None, _err("RRP_MISSING", f"Line {line.line_id}: RRP is required"), warnings
        return {"family": "BASE", "method": "RRP", "rate": line.rrp, "pct": None}, None, warnings
    return None, None, warnings


def _price_list(campaigns: list[Campaign], order: Order, line: OrderLine, value: Decimal):
    """Exclusive customer Price List (Functional Spec §5.6): the active item rate, or a hard error."""
    assigned = order.customer.assigned_price_list_id
    for campaign, rule in family_rules(campaigns, "PRICE_LIST"):
        if assigned and assigned not in (rule.rule_id, campaign.code, campaign.campaign_id):
            continue
        o = RuleOutcome(campaign, rule)
        if not (date_checks(o, order.order_date) and audience_checks(o, order, line, value)) or not o.matched:
            continue
        _, item = item_in_scope(tuple(i for i in rule.items if i.include), line)
        if rule.currency and order.currency and rule.currency.upper() != order.currency.upper():
            return None, _err(
                "PRICE_LIST_CURRENCY_MISMATCH",
                f"Price List {rule.rule_id} is in {rule.currency.upper()}, the order in {order.currency.upper()}",
                priceListId=rule.rule_id,
            )
        if item is None or item.rate is None:
            return None, _err(
                "PRICE_LIST_ITEM_NOT_FOUND",
                f"Price List {rule.rule_id} has no active rate for {line.sku}",
                priceListId=rule.rule_id,
            )
        return {
            "family": "PRICE_LIST",
            "campaign": campaign,
            "rule": rule,
            "tier": None,
            "outcome": o,
            "method": "FIXED_UNIT_RATE",
            "rate": item.rate,
            "pct": None,
            "preCapPct": None,
            "capRule": None,
            "capApplied": False,
            "capBypass": False,
        }, None
    return None, _err(
        "PRICE_LIST_ASSIGNMENT_NOT_FOUND",
        f"No active Price List {assigned or ''} applies to customer {order.customer.customer_id}".replace("  ", " "),
    )


def _bonus(campaigns: list[Campaign], order: Order, line: OrderLine, value: Decimal, winner_family: str) -> list[dict]:
    """Same-item bonus stock (Functional Spec §9): separate from the monetary price."""
    pairs = sorted(
        (
            (c, r)
            for c in campaigns
            for r in c.rules
            if r.bonus and (r.family == "BONUS" or r.rule_type == "BONUS_STOCK")
        ),
        key=lambda p: sort_key(*p),
    )
    for campaign, rule in pairs:
        o = evaluate_rule(campaign, rule, order, line, value)
        if not o.matched or o.qualifying_quantity is None:
            continue
        b = rule.bonus
        if b.permitted_family and b.permitted_family != winner_family:
            continue
        if o.qualifying_quantity < b.buy_quantity:
            continue
        qty = (o.qualifying_quantity // b.buy_quantity) * b.bonus_quantity if b.repeatable else b.bonus_quantity
        return [
            {
                "bonusItemInternalId": line.item_id,
                "bonusItemCode": line.sku,
                "bonusQuantity": qty,
                "bonusUnitRate": Decimal("0.000"),
                "bonusLineAmount": Decimal("0.000"),
                "sourceRuleId": rule.rule_id,
                "sourceRuleVersion": rule.version,
                "sourceOrderLineId": line.line_id,
            }
        ]
    return []


def _line_shell(line: OrderLine) -> dict:
    original = line.current_price if line.current_price is not None else line.base_price
    return {
        "lineId": line.line_id,
        "itemInternalId": line.item_id,
        "sku": line.sku,
        "productName": line.product_name,
        "quantity": line.quantity,
        "lineType": line.line_type,
        "linePricingStatus": "SUCCESS",
        "originalPrice": original,
        "basePrice": None,
        "promotionApplied": False,
        "appliedPricingFamily": None,
        "pricingMethod": None,
        "campaignId": None,
        "campaignName": None,
        "ruleId": None,
        "ruleName": None,
        "appliedRuleVersion": None,
        "appliedTierId": None,
        "tierPosition": None,
        "thresholdBasis": None,
        "qualifyingQuantity": None,
        "discountType": None,
        "discountValue": None,
        "discount": Decimal(0),
        "discountAmount": Decimal(0),
        "preCapDiscountPercentage": None,
        "discountCapApplied": False,
        "appliedCapId": None,
        "capBypassApplied": False,
        "finalDiscountPercentage": None,
        "unroundedUnitRate": None,
        "finalUnitRate": None,
        "finalPrice": original,
        "lineTotal": original * line.quantity if original is not None else None,
        "priceListId": None,
        "bonusStock": [],
        "reason": NO_PROMOTION,
        "errors": [],
        "warnings": [],
        "explanation": {"conditions": [], "checkedRules": [], "rulesNotChecked": 0, "candidates": []},
    }


def price_line(order: Order, line: OrderLine, campaigns: list[Campaign], value: Decimal) -> dict:
    out = _line_shell(line)
    if not line.chargeable:
        out.update(linePricingStatus="BYPASSED", reason="Line excluded from pricing")
        out["warnings"].append(
            _err("LINE_EXCLUDED_FROM_PRICING", f"Line {line.line_id} ({line.line_type}) was bypassed")
        )
        return out
    if line.manual_override:
        if line.existing_manual_rate is None:
            out.update(linePricingStatus="ERROR", reason="Manual override without a rate")
            out["errors"].append(
                _err(
                    "PRICING_CONTEXT_INCOMPLETE",
                    "line.existingManualRate is required for a manual override",
                    attribute="line.existingManualRate",
                )
            )
            return out
        rate = discount.round_rate(line.existing_manual_rate)
        out.update(
            linePricingStatus="MANUAL_OVERRIDE",
            pricingMethod="FIXED_UNIT_RATE",
            unroundedUnitRate=line.existing_manual_rate,
            finalUnitRate=rate,
            finalPrice=rate,
            lineTotal=rate * line.quantity,
            reason="Approved manual rate preserved",
        )
        out["warnings"].append(_err("MANUAL_OVERRIDE_PRESERVED", f"Line {line.line_id}: existing manual rate kept"))
        return out
    if order.customer.price_level_type == "BYPASS_100_PERCENT":
        out.update(linePricingStatus="BYPASSED", reason="Approved engine bypass for this customer")
        return out

    reference = reference_price(line)
    candidates: list[dict] = []
    if order.customer.price_level_type == "PRICE_LIST":
        cand, error = _price_list(campaigns, order, line, value)
        if error:
            out.update(linePricingStatus="ERROR", reason=error["message"])
            out["errors"].append(error)
            return out
        candidates.append(cand)
    else:
        base, error, _ = _base_candidate(order, line)
        if error:
            out.update(linePricingStatus="ERROR", reason=error["message"])
            out["errors"].append(error)
            return out
        cap = _cap_for(campaigns, order, line, value)
        base.update(
            campaign=None,
            rule=None,
            tier=None,
            outcome=None,
            preCapPct=None,
            capRule=None,
            capApplied=False,
            capBypass=False,
        )
        candidates.append(_apply_cap(base, reference, cap, "APPLY_CAP") if base["pct"] else base)
        out["basePrice"] = base["rate"]
        trace, position = [], 0
        for family in MONETARY_FAMILIES:
            if family == "PROMOTION_CODE" and not order.promotion_code:
                continue
            for campaign, rule in family_rules(campaigns, family):
                if rule.bonus and not rule.tiers and not rule.action:
                    continue
                position += 1
                o = evaluate_rule(campaign, rule, order, line, value)
                trace.append(_trace(position, o))
                for m in o.missing:
                    out["errors"].append(
                        _err(
                            "PRICING_CONTEXT_INCOMPLETE",
                            f"{ATTRIBUTE_NAMES.get(m, m)} is required by rule {rule.rule_id}",
                            attribute=ATTRIBUTE_NAMES.get(m, m),
                            ruleId=rule.rule_id,
                        )
                    )
                if o.error:
                    out["errors"].append({**o.error, "ruleId": rule.rule_id})
                if o.matched and reference is not None:
                    candidates.append(_candidate(o, line, reference, cap))
                    if rule.comparison_mode != "BEST_PRICE":
                        break  # an Exclusive rule: the most specific / highest-priority match wins the family
                    # Best Price: keep checking every campaign; the lowest final price wins below.
        out["explanation"]["checkedRules"] = trace

    exclusive = [c for c in candidates if c.get("rule") is not None and c["rule"].comparison_mode == "EXCLUSIVE"]
    pool = exclusive or candidates
    # Lowest final price; on a tie the family order, then the check order (specificity, priority) decides.
    winner = min(pool, key=lambda c: (c["rate"], TIE_ORDER.get(c["family"], 9) if c["family"] != "BASE" else 99))
    final = discount.round_rate(winner["rate"])
    out["explanation"]["candidates"] = [
        {
            "family": c["family"],
            "ruleId": c["rule"].rule_id if c.get("rule") else None,
            "ruleName": c["rule"].name if c.get("rule") else "Base pricing",
            "unitRate": c["rate"],
            "capApplied": c.get("capApplied", False),
            "winner": c is winner,
        }
        for c in candidates
    ]
    pct = winner["pct"]
    out.update(
        appliedPricingFamily=winner["family"],
        pricingMethod=winner["method"],
        unroundedUnitRate=winner["rate"],
        finalUnitRate=final,
        finalPrice=final,
        lineTotal=final * line.quantity,
        finalDiscountPercentage=(pct / 100) if pct is not None else None,
        preCapDiscountPercentage=(winner["preCapPct"] / 100) if winner.get("preCapPct") is not None else None,
        discountCapApplied=winner.get("capApplied", False),
        capBypassApplied=winner.get("capBypass", False),
        appliedCapId=winner["capRule"].rule_id if winner.get("capRule") else None,
    )
    if winner.get("capApplied"):
        out["warnings"].append(
            _err("CAP_APPLIED", f"Discount reduced to the {winner['pct']}% cap ({winner['capRule'].rule_id})")
        )
    if winner.get("capBypass"):
        out["warnings"].append(_err("MONTHLY_PROMO_CAP_BYPASS", f"Approved bypass of cap {winner['capRule'].rule_id}"))
    rule: Rule | None = winner.get("rule")
    if rule is not None:
        tier: Tier | None = winner["tier"]
        o: RuleOutcome = winner["outcome"]
        ref = reference if reference is not None else final
        out.update(
            promotionApplied=winner["family"] != "PRICE_LIST",
            campaignId=winner["campaign"].campaign_id,
            campaignName=winner["campaign"].name,
            ruleId=rule.rule_id,
            ruleName=rule.name,
            appliedRuleVersion=rule.version,
            appliedTierId=(tier.tier_id or f"{rule.rule_id}-T{tier.position}") if tier else None,
            tierPosition=tier.position if tier else None,
            thresholdBasis=rule.quantity_basis,
            qualifyingQuantity=o.qualifying_quantity if o else None,
            discountType=winner["method"].lower(),
            discountValue=pct if pct is not None else final,
            discount=pct if pct is not None else (discount.money((ref - final) / ref * 100) if ref else Decimal(0)),
            discountAmount=discount.money(ref - final) if ref is not None else Decimal(0),
            priceListId=rule.rule_id if winner["family"] == "PRICE_LIST" else None,
            reason=f"{winner['campaign'].name}: {rule.name} ({discount.describe_tier(tier, order.currency) if tier else discount.describe_outcome(rule, order.currency)})",
        )
        out["explanation"]["conditions"] = [f"{c.text} ✓" for c in o.checks] if o else []
        out["explanation"]["action"] = (
            discount.describe_tier(tier, order.currency) if tier else discount.describe_outcome(rule, order.currency)
        )
    else:
        out["reason"] = (
            NO_PROMOTION if winner["method"] == "ORDER_PRICE" else f"Base pricing ({winner['method'].lower()})"
        )
    if winner["family"] != "PRICE_LIST":
        out["bonusStock"] = _bonus(campaigns, order, line, value, winner["family"])
    return out


def evaluate_sales_order(
    order: Order,
    campaigns: list[Campaign],
    controlled: dict[str, list[str]] | None = None,
    eligibility: list[EligibilityRecord] | None = None,
    usage: list[tuple[str, int, int]] | None = None,
) -> dict:
    if eligibility is not None or usage is not None:
        order = replace(
            order,
            eligibility_records=tuple(eligibility) if eligibility is not None else order.eligibility_records,
            code_usage=tuple(usage) if usage is not None else order.code_usage,
        )
    errors = validate_order(order, controlled or {})
    warnings = []
    if order.customer.price_level_type is None:
        warnings.append(
            _err(
                "PRICING_CONTEXT_INCOMPLETE",
                "customer.priceLevelType not supplied: the order's own price is the base candidate",
                attribute="customer.priceLevelType",
            )
        )
    if errors:
        items = []
        for ln in order.lines:
            line = _line_shell(ln)
            line.update(linePricingStatus="ERROR", reason="Order rejected: " + "; ".join(e["message"] for e in errors))
            items.append(line)
    else:
        value = order_value(order)
        items = [price_line(order, ln, campaigns, value) for ln in order.lines]
    original = sum((i["originalPrice"] * i["quantity"] for i in items if i["originalPrice"] is not None), Decimal(0))
    final = sum((i["lineTotal"] for i in items if i["lineTotal"] is not None), Decimal(0))
    applied = sum(1 for i in items if i["promotionApplied"])
    line_errors = any(i["errors"] or i["linePricingStatus"] == "ERROR" for i in items)
    status = "ERROR" if errors else "PARTIAL_SUCCESS" if line_errors else "SUCCESS"
    return {
        "salesOrderId": order.sales_order_id,
        "customerId": order.customer.customer_id,
        "orderDate": order.order_date.isoformat(),
        "currency": order.currency,
        "engineVersion": ENGINE_VERSION,
        "responseStatus": status,
        "pricingStatus": "ERROR" if errors else "PROMOTION_APPLIED" if applied else "NO_PROMOTION",
        "errors": errors,
        "warnings": warnings,
        "items": items,
        "totals": {"original": original, "final": final, "savings": original - final, "promotionLines": applied},
    }
