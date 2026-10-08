"""Rules engine against the Promotion Engine specifications v2.0 (pure: no database)."""

from datetime import date
from decimal import Decimal as D

import pytest

from app.modules.campaigns.schema import RuleIn
from app.modules.pricing.engine import evaluate_sales_order
from app.modules.pricing.model import (
    Action,
    Bonus,
    Campaign,
    Condition,
    Order,
    OrderCustomer,
    OrderLine,
    RateOverride,
    Rule,
    RuleItem,
    Tier,
)

ON = date(2026, 10, 8)


def campaign(*rules, priority=1, cid="CAM-001", **kw):
    return Campaign(cid, f"Campaign {cid}", priority, date(2026, 10, 1), date(2026, 10, 31), tuple(rules), **kw)


def rule(rid="RULE-001", **kw):
    kw.setdefault("priority", 1)
    return Rule(rid, kw.pop("name", rid), **kw)


def line(lid="1", sku="SKU-1", qty=1, **kw):
    return OrderLine(lid, sku, qty, **kw)


def customer(**kw):
    kw.setdefault("customer_id", "CUST1")
    return OrderCustomer(**kw)


def price(lines, campaigns, cust=None, controlled=None, **order_kw):
    order = Order("SO-1", ON, cust or customer(price_level_type="WHOLESALE"), tuple(lines), "AUD", **order_kw)
    return evaluate_sales_order(order, list(campaigns), controlled)


def item(result, i=0):
    return result["items"][i]


TIERS = (Tier(1, D(1), D(6), D(20)), Tier(2, D(6), D(12), D(35)), Tier(3, D(12), None, D(45)))


# --------------------------------------------------------------------------- critical scenarios (§28)
def test_no_promotion_keeps_base_price():
    r = item(price([line(wholesale_price=D(100))], []))
    assert r["finalUnitRate"] == D("100.000") and r["appliedPricingFamily"] == "BASE" and not r["promotionApplied"]


def test_everyday_discount_20_percent():
    c = campaign(rule(action=Action("PERCENTAGE", D(20))))
    r = item(price([line(wholesale_price=D(100))], [c]))
    assert r["finalUnitRate"] == D("80.000") and r["appliedPricingFamily"] == "EVERYDAY" and r["appliedRuleVersion"] == 1


def test_quantity_tier_from_one_rule():
    c = campaign(rule(tiers=TIERS))
    assert item(price([line(qty=6, wholesale_price=D(100))], [c]))["finalUnitRate"] == D("65.000")
    assert item(price([line(qty=3, wholesale_price=D(100))], [c]))["tierPosition"] == 1
    r = item(price([line(qty=12, wholesale_price=D(100))], [c]))
    assert r["tierPosition"] == 3 and r["finalUnitRate"] == D("55.000") and r["appliedTierId"] == "RULE-001-T3"


def test_best_price_wins_across_families():
    every = campaign(rule("R-EVR", action=Action("PERCENTAGE", D(10))), cid="CAM-A")
    monthly = campaign(
        rule("R-MON", family="MONTHLY_PROMO", action=Action("PERCENTAGE", D(20)), conditions=(Condition("channel", "equals", "Pharmacy"),)),
        cid="CAM-B",
        priority=9,
    )
    cust = customer(price_level_type="WHOLESALE", channel="Pharmacy", monthly_promotion_eligible=True)
    r = item(price([line(wholesale_price=D(100))], [every, monthly], cust))
    assert r["finalUnitRate"] == D("80.000") and r["appliedPricingFamily"] == "MONTHLY_PROMO"
    assert sorted(c["unitRate"] for c in r["explanation"]["candidates"]) == [D(80), D(90), D(100)]


def test_no_stacking():
    c = campaign(rule("R1", action=Action("PERCENTAGE", D(20))), rule("R2", family="EXCEPTION", action=Action("PERCENTAGE", D(10))))
    cust = customer(price_level_type="PERCENTAGE", base_discount_percentage=D("0.10"))
    r = item(price([line(wholesale_price=D(100))], [c], cust))
    assert r["finalUnitRate"] == D("80.000")  # best single candidate, never 20 % + 10 % + base 10 %


def test_cap_reduces_discount_and_monthly_bypass():
    cap = campaign(rule("CAP-1", family="CAP", max_discount_percentage=D(25), items=(RuleItem("SB1"),)), cid="CAM-CAP")
    c = campaign(rule(action=Action("PERCENTAGE", D(40))))
    r = item(price([line(item_id="SB1", wholesale_price=D(100))], [c, cap]))
    assert r["discountCapApplied"] and r["finalUnitRate"] == D("75.000") and r["preCapDiscountPercentage"] == D("0.4")
    assert r["appliedCapId"] == "CAP-1"
    monthly = campaign(
        rule(family="MONTHLY_PROMO", cap_treatment="APPROVED_BYPASS", action=Action("PERCENTAGE", D(40)),
             items=(RuleItem("SB1"),), conditions=(Condition("channel", "equals", "Pharmacy"),))
    )
    cust = customer(price_level_type="WHOLESALE", channel="Pharmacy", monthly_promotion_eligible=True)
    r = item(price([line(item_id="SB1", wholesale_price=D(100))], [monthly, cap], cust))
    assert r["capBypassApplied"] and not r["discountCapApplied"] and r["finalUnitRate"] == D("60.000")


def test_specific_cap_overrides_general():
    general = rule("CAP-GEN", family="CAP", max_discount_percentage=D(25), items=(RuleItem("SB1"),), priority=1)
    channel = rule("CAP-DD", family="CAP", max_discount_percentage=D("32.5"), items=(RuleItem("SB1"),), priority=2,
                   conditions=(Condition("channel", "equals", "Domestic Distributor"),))
    c = campaign(rule(action=Action("PERCENTAGE", D(40))))
    cust = customer(price_level_type="WHOLESALE", channel="Domestic Distributor")
    r = item(price([line(item_id="SB1", wholesale_price=D(100))], [c, campaign(general, channel, cid="CAM-CAP")], cust))
    assert r["appliedCapId"] == "CAP-DD" and r["finalUnitRate"] == D("67.500")


def test_excluded_line_is_bypassed_and_not_counted():
    c = campaign(rule(action=Action("PERCENTAGE", D(20))))
    res = price([line(wholesale_price=D(100), exclude_pricing_line=True), line("2", "FREE", line_type="FREE_STOCK")], [c])
    assert {i["linePricingStatus"] for i in res["items"]} == {"BYPASSED"} and not item(res)["promotionApplied"]
    assert item(res)["warnings"][0]["code"] == "LINE_EXCLUDED_FROM_PRICING"


def test_manual_override_preserved():
    c = campaign(rule(action=Action("PERCENTAGE", D(50))))
    r = item(price([line(wholesale_price=D(100), manual_override=True, existing_manual_rate=D("70.1234"))], [c]))
    assert r["linePricingStatus"] == "MANUAL_OVERRIDE" and r["finalUnitRate"] == D("70.123") and not r["promotionApplied"]
    r = item(price([line(wholesale_price=D(100), manual_override=True)], [c]))
    assert r["linePricingStatus"] == "ERROR" and r["errors"][0]["code"] == "PRICING_CONTEXT_INCOMPLETE"


# --------------------------------------------------------------------------- audience, programme, items
def test_banner_marketing_flag_and_specificity():
    general = rule("R-CH", action=Action("PERCENTAGE", D(35)), conditions=(Condition("channel", "equals", "Pharmacy"),))
    banner = rule("R-TW", action=Action("PERCENTAGE", D(30)), priority=5, conditions=(Condition("banner", "equals", "TerryWhite"),))
    flag = rule("R-MF", action=Action("PERCENTAGE", D(32)), conditions=(Condition("marketing_flag", "in", ["TW"]),))
    cust = customer(price_level_type="WHOLESALE", channel="Pharmacy", banner="TerryWhite", marketing_flag="TW")
    r = item(price([line(wholesale_price=D(100))], [campaign(general, banner, flag)], cust))
    assert r["ruleId"] == "R-TW"  # Banner beats Marketing Flag beats Channel within the family, despite priority


def test_programme_eligibility_needed_but_not_sufficient():
    accel = rule(programme_code="ACCELERATE", tiers=(Tier(1, D(6), D(12), D(35)), Tier(2, D(12), None, D(45))),
                 conditions=(Condition("channel", "equals", "Pharmacy"), Condition("accelerate_flag", "equals", True)))
    c = campaign(accel)
    base = dict(price_level_type="WHOLESALE", channel="Pharmacy")
    ok = item(price([line(qty=6, item_id="I1", accelerate_flag=True, wholesale_price=D(20))], [c], customer(**base, accelerate_eligible=True)))
    assert ok["finalUnitRate"] == D("13.000")
    no = item(price([line(qty=6, item_id="I1", accelerate_flag=True, wholesale_price=D(20))], [c], customer(**base, accelerate_eligible=False)))
    assert not no["promotionApplied"]
    low = item(price([line(qty=2, item_id="I1", accelerate_flag=True, wholesale_price=D(20))], [c], customer(**base, accelerate_eligible=True)))
    assert not low["promotionApplied"]  # eligible, but the rule does not match


def test_item_internal_id_include_exclude_and_rate_override():
    r1 = rule(tiers=(Tier(1, D(6), None, D(35)),), items=(RuleItem("430000"), RuleItem("430001"), RuleItem("999", include=False)),
              rate_overrides=(RateOverride("430001", 1, D(45)),))
    c = campaign(r1)
    res = price([line("1", "A", 6, item_id="430000", wholesale_price=D(20)), line("2", "B", 6, item_id="430001", wholesale_price=D(20)),
                 line("3", "C", 6, item_id="500", wholesale_price=D(20))], [c])
    assert [i["finalUnitRate"] for i in res["items"]] == [D("13.000"), D("11.000"), D("20.000")]


def test_mts_requires_eligibility_and_selected_item():
    mts = rule(programme_code="MTS", action=Action("PERCENTAGE", D(50)), items=(RuleItem("M1"),))
    cust = customer(price_level_type="WHOLESALE", mts_eligible=True)
    res = price([line("1", "A", 1, item_id="M1", wholesale_price=D(10)), line("2", "B", 1, item_id="M2", wholesale_price=D(10))], [campaign(mts)], cust)
    assert [i["promotionApplied"] for i in res["items"]] == [True, False]


# --------------------------------------------------------------------------- quantity bases
def test_mixed_pool_counts_and_discounts_only_selected_items():
    mixed = rule(quantity_basis="DIRECT_ITEM_GROUP_QUANTITY", mixed_pool_id="P1", tiers=(Tier(1, D(15), None, D(30)),),
                 items=(RuleItem("A", mixed_pool_id="P1"), RuleItem("B", mixed_pool_id="P1"), RuleItem("C", mixed_pool_id="P1")))
    lines = [line(str(i), s, 5, item_id=s, wholesale_price=D(10)) for i, s in enumerate("ABCD", 1)]
    res = price(lines, [campaign(mixed)])
    assert [i["promotionApplied"] for i in res["items"]] == [True, True, True, False]
    assert item(res)["qualifyingQuantity"] == D(15) and item(res)["thresholdBasis"] == "DIRECT_ITEM_GROUP_QUANTITY"
    assert not item(price(lines[:2], [campaign(mixed)]))["promotionApplied"]  # pool of 10 < 15


def test_order_qualifying_quantity():
    r1 = rule(quantity_basis="ORDER_QUALIFYING_QUANTITY", tiers=(Tier(1, D(10), None, D(10)),),
              conditions=(Condition("item_group", "equals", "FG"),))
    lines = [line("1", "A", 6, item_group="FG", wholesale_price=D(10)), line("2", "B", 4, item_group="FG", wholesale_price=D(10)),
             line("3", "C", 50, item_group="OTHER", wholesale_price=D(10))]
    res = price(lines, [campaign(r1)])
    assert [i["promotionApplied"] for i in res["items"]] == [True, True, False]


def test_shipper_quantity_livelife():
    live = rule(quantity_basis="SHIPPER_QUANTITY", tiers=(Tier(1, D(1), None, D(40)),), conditions=(Condition("banner", "equals", "LiveLife"),))
    cust = customer(price_level_type="WHOLESALE", banner="LiveLife")
    ok = item(price([line(qty=13, shipper_quantity=D(12), wholesale_price=D(10))], [campaign(live)], cust))
    assert ok["finalUnitRate"] == D("6.000")
    assert not item(price([line(qty=11, shipper_quantity=D(12), wholesale_price=D(10))], [campaign(live)], cust))["promotionApplied"]
    bad = item(price([line(qty=13, wholesale_price=D(10))], [campaign(live)], cust))
    assert bad["errors"][0]["code"] == "INVALID_SHIPPER_QUANTITY"


# --------------------------------------------------------------------------- base pricing, price list, bonus
def test_base_price_methods_and_rounding():
    pct = customer(price_level_type="PERCENTAGE", base_discount_percentage=D("0.2"))
    assert item(price([line(wholesale_price=D("20.00"))], [], pct))["finalUnitRate"] == D("16.000")
    assert item(price([line(wholesale_price=D("10.0005"))], [], customer(price_level_type="WHOLESALE")))["finalUnitRate"] == D("10.001")
    assert item(price([line(rrp=D(30))], [], customer(price_level_type="RRP")))["finalUnitRate"] == D("30.000")
    missing = item(price([line()], [], pct))
    assert missing["linePricingStatus"] == "ERROR" and missing["errors"][0]["code"] == "WHOLESALE_PRICE_MISSING"
    assert item(price([line()], [], customer(price_level_type="RRP")))["errors"][0]["code"] == "RRP_MISSING"


def test_exclusive_price_list_blocks_everything_else():
    pl = rule("PL-1", family="PRICE_LIST", comparison_mode="EXCLUSIVE", items=(RuleItem("I1", rate=D("12.3456")),))
    promo = rule("R-50", action=Action("PERCENTAGE", D(90)))
    cust = customer(price_level_type="PRICE_LIST", assigned_price_list_id="PL-1")
    r = item(price([line(item_id="I1", wholesale_price=D(100))], [campaign(pl, promo)], cust))
    assert r["appliedPricingFamily"] == "PRICE_LIST" and r["finalUnitRate"] == D("12.346") and r["bonusStock"] == []
    assert item(price([line(item_id="I2")], [campaign(pl)], cust))["errors"][0]["code"] == "PRICE_LIST_ITEM_NOT_FOUND"
    other = customer(price_level_type="PRICE_LIST", assigned_price_list_id="PL-X")
    assert item(price([line(item_id="I1")], [campaign(pl)], other))["errors"][0]["code"] == "PRICE_LIST_ASSIGNMENT_NOT_FOUND"


def test_bonus_stock_repeatable_and_suppressed_by_promotion():
    bonus = rule("B-1", family="BONUS", rule_type="BONUS_STOCK", bonus=Bonus(D(10), D(1), True, "BASE"), items=(RuleItem("I1"),))
    r = item(price([line(qty=25, item_id="I1", wholesale_price=D(10))], [campaign(bonus)]))
    assert r["bonusStock"][0]["bonusQuantity"] == D(2) and r["finalUnitRate"] == D("10.000")
    promo = rule("R-1", action=Action("PERCENTAGE", D(20)))
    r = item(price([line(qty=25, item_id="I1", wholesale_price=D(10))], [campaign(bonus, promo)]))
    assert r["bonusStock"] == []  # a winning Everyday result suppresses a base-price bonus


def test_promotion_code_only_with_matching_code():
    code = rule(family="PROMOTION_CODE", promotion_code="SPRING", action=Action("PERCENTAGE", D(15)))
    assert item(price([line(wholesale_price=D(100))], [campaign(code)], promotion_code="spring"))["finalUnitRate"] == D("85.000")
    assert item(price([line(wholesale_price=D(100))], [campaign(code)]))["finalUnitRate"] == D("100.000")


# --------------------------------------------------------------------------- validation (§19)
def test_duplicate_chargeable_item_code_rejected():
    res = price([line("1", "A", wholesale_price=D(1)), line("2", "A", wholesale_price=D(1))], [])
    assert res["responseStatus"] == "ERROR" and res["errors"][0]["code"] == "DUPLICATE_CHARGEABLE_ITEM_CODE"
    ok = price([line("1", "A", wholesale_price=D(1)), line("2", "A", line_type="FREE_STOCK")], [])
    assert ok["responseStatus"] == "SUCCESS"


def test_missing_attribute_is_an_error_not_a_guess():
    c = campaign(rule(action=Action("PERCENTAGE", D(20)), conditions=(Condition("channel", "equals", "Pharmacy"),)))
    res = price([line(wholesale_price=D(100))], [c])
    assert res["responseStatus"] == "PARTIAL_SUCCESS"
    assert item(res)["errors"][0]["code"] == "PRICING_CONTEXT_INCOMPLETE" and not item(res)["promotionApplied"]


def test_invalid_controlled_attribute_and_submission_status():
    cust = customer(price_level_type="WHOLESALE", channel="Pharmcy")
    res = price([line(wholesale_price=D(1))], [], cust, {"channel": ["Pharmacy", "Health Food"]})
    assert res["errors"][0]["code"] == "INVALID_CONTROLLED_ATTRIBUTE"
    res = price([line(wholesale_price=D(1))], [], order_status="Pending Fulfilment", pricing_status="Pricing Hold")
    assert res["errors"][0]["code"] == "ORDER_NOT_ELIGIBLE_FOR_SUBMISSION"


# --------------------------------------------------------------------------- existing behaviour kept
def test_existing_rules_without_price_level_use_order_price():
    """Orders that do not send priceLevelType (today's NetSuite cart) price as before, with a warning."""
    c = campaign(rule(action=Action("PERCENTAGE", D(35)), conditions=(Condition("quantity", "gte", D(6)),)))
    res = price([line(qty=6, base_price=D("32.10"), current_price=D("28.95"))], [c], customer())
    r = item(res)
    assert r["finalPrice"] == D("20.865") and r["promotionApplied"] and r["discount"] == D(35)
    assert res["warnings"][0]["attribute"] == "customer.priceLevelType"
    r = item(price([line(qty=1, base_price=D("32.10"), current_price=D("28.95"))], [c], customer()))
    assert r["finalPrice"] == D("28.950") and not r["promotionApplied"]


def test_existing_customer_sku_product_conditions():
    c = campaign(rule(action=Action("FIXED_UNIT_PRICE", D(5)),
                      conditions=(Condition("customer_id", "equals", "CUST1"), Condition("sku", "in", ["A", "B"]),
                                  Condition("product_name", "equals", "Zinc"))))
    res = price([line("1", "A", product_name="Zinc", wholesale_price=D(10)), line("2", "C", product_name="Zinc", wholesale_price=D(10))], [c])
    assert [i["finalUnitRate"] for i in res["items"]] == [D("5.000"), D("10.000")]


# --------------------------------------------------------------------------- rule structure validation
def test_rule_validation():
    with pytest.raises(ValueError, match="Monthly Promotion needs at least one included item"):
        RuleIn(name="M", family="MONTHLY_PROMO", action={"action_type": "PERCENTAGE", "value": 10})
    with pytest.raises(ValueError, match="either a discount percentage or a fixed unit rate"):
        RuleIn(name="T", tiers=[{"position": 1, "min_quantity": 1, "discount_percentage": 10, "fixed_unit_rate": 5}])
    with pytest.raises(ValueError, match="overlaps"):
        RuleIn(name="T", tiers=[{"position": 1, "min_quantity": 1, "max_quantity": 8, "discount_percentage": 10},
                                {"position": 2, "min_quantity": 6, "discount_percentage": 20}])
    r = RuleIn(name="T", quantity_basis="SHIPPER_QUANTITY", tiers=[{"position": 1, "min_quantity": 1, "discount_percentage": 40}])
    assert r.rule_type == "SHIPPER_QUANTITY" and r.family == "EVERYDAY" and r.comparison_mode == "BEST_PRICE"
    pl = RuleIn(name="PL", family="PRICE_LIST", items=[{"item_id": "1", "rate": 5}])
    assert pl.comparison_mode == "EXCLUSIVE" and pl.rule_type == "FIXED_UNIT_RATE"
