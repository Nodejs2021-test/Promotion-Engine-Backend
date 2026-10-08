"""Inputs of the rules engine and the vocabulary a business user builds rules from.

Rule structure follows the Promotion Engine Data and API Field Specification v2.0 (§3-§10): a rule header (family,
type, comparison mode, cap treatment, priority, validity, version), audience and item attribute conditions, direct
item selection, a quantity control, tiers and an outcome (percentage / fixed unit rate, cap, or bonus stock).
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

ENGINE_VERSION = "2.0"

# --------------------------------------------------------------------------- rule header vocabulary (§3)
RULE_FAMILIES: dict[str, str] = {
    "EVERYDAY": "Everyday Deal",
    "MONTHLY_PROMO": "Monthly Promotion",
    "PROMOTION_CODE": "Promotion Code",
    "EXCEPTION": "Customer Exception",
    "PRICE_LIST": "Customer Price List",
    "CAP": "Item Discount Cap",
    "BONUS": "Bonus Stock",
}
# Families that produce a monetary candidate competing with the base price.
MONETARY_FAMILIES = ("MONTHLY_PROMO", "PROMOTION_CODE", "EVERYDAY", "EXCEPTION")
RULE_TYPES: dict[str, str] = {
    "QUANTITY_TIER": "Quantity tiers",
    "MIXED_QUANTITY": "Mixed-item quantity",
    "SHIPPER_QUANTITY": "Shipper quantity",
    "FLAT_OVERRIDE": "Flat override",
    "FIXED_UNIT_RATE": "Fixed unit rate",
    "BONUS_STOCK": "Bonus stock",
    "ELIGIBILITY_CONTROL": "Eligibility control (cap)",
}
COMPARISON_MODES: dict[str, str] = {"BEST_PRICE": "Best Price", "EXCLUSIVE": "Exclusive"}
CAP_TREATMENTS: dict[str, str] = {"APPLY_CAP": "Apply item cap", "APPROVED_BYPASS": "Approved cap bypass"}
QUANTITY_BASES: dict[str, str] = {
    "LINE_QUANTITY": "Line quantity",
    "DIRECT_ITEM_GROUP_QUANTITY": "Mixed pool quantity (selected items)",
    "ORDER_QUALIFYING_QUANTITY": "Order qualifying quantity",
    "SHIPPER_QUANTITY": "Shipper quantity",
    "ANY_QUANTITY": "Any quantity",
}
ITEM_ROLES: dict[str, str] = {
    "STANDARD": "Standard",
    "RATE_OVERRIDE": "Rate override",
    "CAP_ITEM": "Cap item",
    "BONUS_ITEM": "Bonus item",
    "EXCEPTION_ITEM": "Exception item",
}
PROGRAMMES: dict[str, str] = {
    "ACCELERATE": "Accelerate",
    "MTS": "Make the Switch",
    "MONTHLY_PROMO": "Monthly Promotion",
}
PRICE_LEVEL_TYPES = ("PERCENTAGE", "WHOLESALE", "RRP", "PRICE_LIST", "BYPASS_100_PERCENT")
CHARGEABLE = "CHARGEABLE_ITEM"
LINE_TYPES = (CHARGEABLE, "FREE_STOCK", "NOTE", "MARKETING_POS", "FREIGHT", "TAX", "DISCOUNT", "ADMINISTRATIVE")

# --------------------------------------------------------------------------- condition fields (§4, §6)
CUSTOMER_FIELDS: dict[str, str] = {
    "customer_id": "Customer",
    "customer_group": "Customer Group",
    "banner": "Banner",
    "marketing_flag": "Marketing Flag",
    "channel": "Channel",
    "accelerate_eligible": "Accelerate Eligible",
    "monthly_promotion_eligible": "Monthly Promotion Eligible",
    "mts_eligible": "MTS Eligible",
}
PRODUCT_FIELDS: dict[str, str] = {
    "item_id": "Item Internal ID",
    "sku": "SKU",
    "product_name": "Product",
    "item_group": "Item Group",
    "item_flag": "Item Flag",
    "category": "Category",
    "accelerate_flag": "Accelerate Flag",
    "commodity_flag": "Commodity Flag",
    "shipper_quantity": "Shipper Quantity",
}
ORDER_FIELDS: dict[str, str] = {
    "quantity": "Quantity",
    "order_value": "Order Value",
}
FIELD_LABELS = {**CUSTOMER_FIELDS, **PRODUCT_FIELDS, **ORDER_FIELDS}
BOOLEAN_FIELDS = frozenset(
    {"accelerate_eligible", "monthly_promotion_eligible", "mts_eligible", "accelerate_flag", "commodity_flag"}
)
# Programme code -> the customer eligibility attribute supplied in the Sales Order (§14.2).
PROGRAMME_FIELD = {
    "ACCELERATE": "accelerate_eligible",
    "MTS": "mts_eligible",
    "MONTHLY_PROMO": "monthly_promotion_eligible",
}
# Audience specificity within a family (Functional Spec §15): lower is more specific; 5 = general rule.
SPECIFICITY_RANK = {"customer_id": 1, "banner": 2, "marketing_flag": 3, "customer_group": 3, "channel": 4}
GENERAL_RANK = 5

TEXT_OPERATORS: dict[str, str] = {
    "equals": "equals",
    "not_equals": "does not equal",
    "in": "is one of",
    "not_in": "is not one of",
}
NUMBER_OPERATORS: dict[str, str] = {
    "gte": "greater than or equal to",
    "lte": "less than or equal to",
}
# Operators allowed per field.
FIELD_OPERATORS: dict[str, tuple[str, ...]] = {
    **{f: tuple(TEXT_OPERATORS) for f in (*CUSTOMER_FIELDS, *PRODUCT_FIELDS)},
    **{f: ("equals",) for f in BOOLEAN_FIELDS},
    "shipper_quantity": ("gte",),
    "quantity": ("gte", "lte"),
    "order_value": ("gte",),
}
OPERATOR_SYMBOLS = {
    "equals": "=",
    "not_equals": "≠",
    "in": "is one of",
    "not_in": "is not one of",
    "gte": ">=",
    "lte": "<=",
}

ACTION_TYPES: dict[str, str] = {
    "PERCENTAGE": "Percentage Discount",
    "FIXED_DISCOUNT": "Fixed Discount (amount off per unit)",
    "PROMOTIONAL_PRICE": "Promotional Price",
    "FIXED_UNIT_PRICE": "Fixed Unit Price",
}


@dataclass(frozen=True)
class Condition:
    field: str
    operator: str
    value: Any  # str for equals / not_equals, list[str] for in / not_in, Decimal for gte / lte, bool for flags


@dataclass(frozen=True)
class Action:
    action_type: str
    value: Decimal


@dataclass(frozen=True)
class RuleItem:
    """An item selected directly in the rule (Item Scope Spec §3)."""

    item_id: str | None
    item_code: str | None = None
    item_name: str | None = None
    include: bool = True
    mixed_pool_id: str | None = None
    role: str = "STANDARD"
    rate: Decimal | None = None  # fixed rate of this item (Price List item rate)
    active: bool = True


@dataclass(frozen=True)
class Tier:
    """A quantity tier (§7.2): minimum inclusive, maximum exclusive; one complete outcome."""

    position: int
    min_quantity: Decimal
    max_quantity: Decimal | None = None
    discount_percentage: Decimal | None = None  # 35 = 35 %
    fixed_unit_rate: Decimal | None = None
    tier_id: str = ""


@dataclass(frozen=True)
class RateOverride:
    """Item rate override for one tier (§8, e.g. the two Glucosamine Accelerate SKUs)."""

    item_id: str
    tier_position: int
    discount_percentage: Decimal


@dataclass(frozen=True)
class Bonus:
    """Same-item bonus stock (§10)."""

    buy_quantity: Decimal
    bonus_quantity: Decimal
    repeatable: bool = True
    permitted_family: str | None = "BASE"  # winning pricing family the bonus requires; None = any


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    priority: int  # lower value has stronger precedence
    action: Action | None = None  # single outcome (when the rule has no tiers)
    conditions: tuple[Condition, ...] = ()
    active: bool = True
    start_date: date | None = None
    end_date: date | None = None
    family: str = "EVERYDAY"
    rule_type: str = "QUANTITY_TIER"
    comparison_mode: str = "BEST_PRICE"
    cap_treatment: str = "APPLY_CAP"
    version: int = 1
    programme_code: str | None = None
    promotion_code: str | None = None
    quantity_basis: str = "LINE_QUANTITY"
    mixed_pool_id: str | None = None
    required_shipper_multiple: Decimal = Decimal(1)
    items: tuple[RuleItem, ...] = ()
    tiers: tuple[Tier, ...] = ()
    rate_overrides: tuple[RateOverride, ...] = ()
    max_discount_percentage: Decimal | None = None  # CAP family
    bonus: Bonus | None = None


@dataclass(frozen=True)
class Campaign:
    campaign_id: str
    name: str
    priority: int  # 1 is checked first
    start_date: date
    end_date: date
    rules: tuple[Rule, ...]
    code: str = ""
    approved: bool = True
    enabled: bool = True
    # Eligibility: an empty list means "all".
    customer_ids: frozenset[str] = frozenset()
    customer_groups: frozenset[str] = frozenset()
    channels: frozenset[str] = frozenset()
    skus: frozenset[str] = frozenset()
    item_groups: frozenset[str] = frozenset()


@dataclass(frozen=True)
class OrderCustomer:
    customer_id: str
    name: str | None = None
    customer_group: str | None = None
    channel: str | None = None
    banner: str | None = None
    marketing_flag: str | None = None
    price_level_type: str | None = None
    base_discount_percentage: Decimal | None = None  # fraction, 0.20 = 20 % (§14.2)
    accelerate_eligible: bool | None = None
    monthly_promotion_eligible: bool | None = None
    mts_eligible: bool | None = None
    assigned_price_list_id: str | None = None


@dataclass(frozen=True)
class OrderLine:
    line_id: str
    sku: str
    quantity: int
    base_price: Decimal | None = None
    current_price: Decimal | None = None
    product_name: str | None = None
    item_group: str | None = None
    item_flag: str | None = None
    item_id: str | None = None
    category: str | None = None
    accelerate_flag: bool | None = None
    commodity_flag: bool | None = None
    wholesale_price: Decimal | None = None
    rrp: Decimal | None = None
    shipper_quantity: Decimal | None = None
    line_type: str = CHARGEABLE
    exclude_pricing_line: bool = False
    manual_override: bool = False
    existing_manual_rate: Decimal | None = None

    @property
    def chargeable(self) -> bool:
        return self.line_type == CHARGEABLE and not self.exclude_pricing_line and self.quantity > 0


@dataclass(frozen=True)
class Order:
    sales_order_id: str
    order_date: date
    customer: OrderCustomer
    lines: tuple[OrderLine, ...]
    currency: str = ""
    promotion_code: str | None = None
    order_status: str | None = None
    pricing_status: str | None = None


@dataclass
class Check:
    """One check of one rule: its text and whether it passed."""

    text: str
    passed: bool


@dataclass
class RuleOutcome:
    campaign: Campaign
    rule: Rule
    checks: list[Check] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # attributes the order lacks (PRICING_CONTEXT_INCOMPLETE)
    tier: Tier | None = None
    qualifying_quantity: Decimal | None = None
    error: dict | None = None  # e.g. INVALID_SHIPPER_QUANTITY

    @property
    def matched(self) -> bool:
        return not self.missing and self.error is None and all(c.passed for c in self.checks)
