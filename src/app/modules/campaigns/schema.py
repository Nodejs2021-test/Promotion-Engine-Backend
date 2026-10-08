"""Campaign and rule request models."""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.pricing.conditions import to_bool
from app.modules.pricing.model import (
    ACTION_TYPES,
    BOOLEAN_FIELDS,
    CAP_TREATMENTS,
    COMPARISON_MODES,
    CUSTOMER_FIELDS,
    FIELD_LABELS,
    FIELD_OPERATORS,
    ITEM_ROLES,
    NUMBER_OPERATORS,
    PROGRAMMES,
    QUANTITY_BASES,
    RULE_FAMILIES,
    RULE_TYPES,
)

TextList = list[str]


def _clean_list(values: Any) -> list[str]:
    """Trimmed, non-empty, de-duplicated values (a comma separated string is accepted too)."""
    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")
    out: list[str] = []
    for v in values:
        s = str(v).strip()
        if s and s.casefold() not in {o.casefold() for o in out}:
            out.append(s)
    return out


class ConditionIn(BaseModel):
    field: str
    operator: str
    value: Any

    @model_validator(mode="after")
    def _check(self):
        if self.field not in FIELD_OPERATORS:
            raise ValueError(f"Unknown condition field '{self.field}'")
        if self.operator not in FIELD_OPERATORS[self.field]:
            allowed = ", ".join(FIELD_OPERATORS[self.field])
            raise ValueError(f"{FIELD_LABELS[self.field]}: operator must be one of {allowed}")
        if self.field in BOOLEAN_FIELDS:
            flag = to_bool(self.value)
            if flag is None:
                raise ValueError(f"{FIELD_LABELS[self.field]}: choose Yes or No")
            self.value = flag
        elif self.operator in NUMBER_OPERATORS:
            try:
                number = Decimal(str(self.value))
            except Exception:  # noqa: BLE001 - any unparsable value
                raise ValueError(f"{FIELD_LABELS[self.field]}: the value must be a number") from None
            if number < 0 or (self.field == "quantity" and number != number.to_integral_value()):
                raise ValueError(f"{FIELD_LABELS[self.field]}: the value must be a positive whole number")
            self.value = number
        elif self.operator in ("in", "not_in"):
            self.value = _clean_list(self.value)
            if not self.value:
                raise ValueError(f"{FIELD_LABELS[self.field]}: enter at least one value")
        else:
            self.value = str(self.value or "").strip()
            if not self.value:
                raise ValueError(f"{FIELD_LABELS[self.field]}: enter a value")
        return self


class ActionIn(BaseModel):
    action_type: str
    value: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def _check(self):
        if self.action_type not in ACTION_TYPES:
            raise ValueError(f"Unknown action '{self.action_type}'")
        if self.action_type == "PERCENTAGE" and self.value > 100:
            raise ValueError("A percentage discount cannot be more than 100")
        return self


class RuleItemIn(BaseModel):
    """An item selected directly in the rule (Item Scope Spec §3)."""

    item_id: str | None = Field(None, max_length=64, description="Item Internal ID: the primary runtime match key")
    item_code: str | None = Field(None, max_length=64)
    item_name: str | None = Field(None, max_length=200)
    include: bool = True
    mixed_pool_id: str | None = Field(None, max_length=40)
    role: str = "STANDARD"
    rate: Decimal | None = Field(None, ge=0, description="Fixed item rate (Price List)")
    active: bool = True

    @model_validator(mode="after")
    def _check(self):
        for f in ("item_id", "item_code", "item_name", "mixed_pool_id"):
            v = getattr(self, f)
            setattr(self, f, (str(v).strip() or None) if v is not None else None)
        if not self.item_id and not self.item_code:
            raise ValueError("A selected item needs an Item Internal ID or an Item Code")
        if self.role not in ITEM_ROLES:
            raise ValueError(f"Unknown item role '{self.role}'")
        return self


class TierIn(BaseModel):
    """A quantity tier (Data Spec §7.2): minimum inclusive, maximum exclusive."""

    position: int = Field(ge=1)
    min_quantity: Decimal = Field(ge=0)
    max_quantity: Decimal | None = Field(None, gt=0)
    discount_percentage: Decimal | None = Field(None, gt=0, le=100)
    fixed_unit_rate: Decimal | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _check(self):
        if (self.discount_percentage is None) == (self.fixed_unit_rate is None):
            raise ValueError(f"Tier {self.position}: enter either a discount percentage or a fixed unit rate")
        if self.max_quantity is not None and self.max_quantity <= self.min_quantity:
            raise ValueError(f"Tier {self.position}: the maximum must be above the minimum")
        return self


class RateOverrideIn(BaseModel):
    item_id: str = Field(min_length=1, max_length=64)
    tier_position: int = Field(ge=1)
    discount_percentage: Decimal = Field(gt=0, le=100)


class BonusIn(BaseModel):
    buy_quantity: Decimal = Field(gt=0)
    bonus_quantity: Decimal = Field(gt=0)
    repeatable: bool = True
    permitted_family: str | None = "BASE"


def _derive_type(rule: "RuleIn") -> str:
    if rule.bonus and not rule.tiers and not rule.action:
        return "BONUS_STOCK"
    if rule.family == "CAP":
        return "ELIGIBILITY_CONTROL"
    if rule.family == "PRICE_LIST":
        return "FIXED_UNIT_RATE"
    if rule.quantity_basis == "DIRECT_ITEM_GROUP_QUANTITY":
        return "MIXED_QUANTITY"
    if rule.quantity_basis == "SHIPPER_QUANTITY":
        return "SHIPPER_QUANTITY"
    if rule.tiers:
        return "FIXED_UNIT_RATE" if all(t.fixed_unit_rate is not None for t in rule.tiers) else "QUANTITY_TIER"
    return "FLAT_OVERRIDE"


class RuleIn(BaseModel):
    """A rule (Data Spec §3-§10). ``action`` is a single outcome; ``tiers`` replace it with quantity tiers."""

    name: str = Field(min_length=1, max_length=120)
    active: bool = True
    start_date: date | None = None
    end_date: date | None = None
    conditions: list[ConditionIn] = Field(default_factory=list, max_length=40)
    action: ActionIn | None = None
    family: str = "EVERYDAY"
    rule_type: str | None = None
    comparison_mode: str = "BEST_PRICE"
    cap_treatment: str = "APPLY_CAP"
    programme_code: str | None = None
    promotion_code: str | None = Field(None, max_length=40)
    quantity_basis: str = "LINE_QUANTITY"
    mixed_pool_id: str | None = Field(None, max_length=40)
    required_shipper_multiple: Decimal = Field(Decimal(1), gt=0)
    items: list[RuleItemIn] = Field(default_factory=list, max_length=2000)
    tiers: list[TierIn] = Field(default_factory=list, max_length=20)
    rate_overrides: list[RateOverrideIn] = Field(default_factory=list, max_length=200)
    max_discount_percentage: Decimal | None = Field(None, ge=0, le=100)
    bonus: BonusIn | None = None
    source_type: str | None = Field(None, max_length=40)
    source_reference: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _check(self):  # noqa: C901 - one place for every rule-structure validation
        self.name = self.name.strip()
        for name, value, allowed in (
            ("family", self.family, RULE_FAMILIES),
            ("comparison mode", self.comparison_mode, COMPARISON_MODES),
            ("cap treatment", self.cap_treatment, CAP_TREATMENTS),
            ("quantity basis", self.quantity_basis, QUANTITY_BASES),
        ):
            if value not in allowed:
                raise ValueError(f"Unknown {name} '{value}'")
        if self.programme_code and self.programme_code not in PROGRAMMES:
            raise ValueError(f"Unknown programme '{self.programme_code}'")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("The rule's end date is before its start date")
        q = {c.operator: c.value for c in self.conditions if c.field == "quantity"}
        if "gte" in q and "lte" in q and q["gte"] > q["lte"]:
            raise ValueError("The minimum quantity is above the maximum quantity")
        included = [i for i in self.items if i.include and i.active]
        positions = [t.position for t in self.tiers]
        if len(set(positions)) != len(positions):
            raise ValueError("Tier positions must be unique")
        ordered = sorted(self.tiers, key=lambda t: t.min_quantity)
        if [t.position for t in ordered] != sorted(positions):
            raise ValueError("Tiers must ascend: a higher tier position needs a higher minimum quantity")
        for a, b in zip(ordered, ordered[1:], strict=False):
            if a.max_quantity is not None and a.max_quantity > b.min_quantity:
                raise ValueError(f"Tier {a.position} overlaps tier {b.position}")
        if self.bonus and self.bonus.permitted_family not in (None, "BASE", *RULE_FAMILIES):
            raise ValueError(f"Unknown permitted winning family '{self.bonus.permitted_family}'")
        if self.family == "CAP":
            if self.max_discount_percentage is None:
                raise ValueError("A cap rule needs the maximum discount percentage")
            if not included:
                raise ValueError("A cap rule needs at least one included item")
        elif self.family == "PRICE_LIST":
            if not any(i.rate is not None for i in included):
                raise ValueError("A Price List needs at least one included item with a rate")
            self.comparison_mode = "EXCLUSIVE"
        elif self.family == "BONUS":
            if not self.bonus:
                raise ValueError("A bonus rule needs Buy Quantity and Bonus Quantity")
        elif not (self.tiers or self.action or self.bonus):
            raise ValueError("Add an outcome: quantity tiers or a single discount")
        if self.family == "PROMOTION_CODE" and not (self.promotion_code or "").strip():
            raise ValueError("A promotion-code rule needs the promotion code")
        if self.family == "MONTHLY_PROMO" and not included:
            raise ValueError("A Monthly Promotion needs at least one included item")
        if self.programme_code == "MTS" and not included:
            raise ValueError("An MTS rule needs its selected items")
        if self.quantity_basis == "DIRECT_ITEM_GROUP_QUANTITY":
            pool = [i for i in included if not self.mixed_pool_id or i.mixed_pool_id == self.mixed_pool_id]
            if not pool:
                raise ValueError("A mixed deal needs included items assigned to its Mixed Pool ID")
        if not set(positions) >= {o.tier_position for o in self.rate_overrides}:
            raise ValueError("A rate override refers to a tier the rule does not have")
        self.promotion_code = (self.promotion_code or "").strip().upper() or None
        self.rule_type = self.rule_type or _derive_type(self)
        if self.rule_type not in RULE_TYPES:
            raise ValueError(f"Unknown rule type '{self.rule_type}'")
        return self

    def has_audience(self) -> bool:
        return any(c.field in CUSTOMER_FIELDS and c.operator in ("equals", "in") for c in self.conditions)


class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=2, max_length=40)
    description: str | None = Field(None, max_length=1000)
    start_date: date
    end_date: date
    customer_ids: TextList = []
    customer_groups: TextList = []
    channels: TextList = []
    skus: TextList = []
    item_groups: TextList = []

    _lists = field_validator("customer_ids", "customer_groups", "channels", "skus", "item_groups", mode="before")(
        lambda v: _clean_list(v)
    )

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        v = v.strip().upper()
        if not all(ch.isalnum() or ch in "-_" for ch in v):
            raise ValueError("Use letters, digits, '-' and '_' only")
        return v

    @model_validator(mode="after")
    def _check(self):
        self.name = self.name.strip()
        if self.start_date > self.end_date:
            raise ValueError("The end date is before the start date")
        return self


class CommentIn(BaseModel):
    comment: str | None = Field(None, max_length=1000)


class OrderIn(BaseModel):
    """An ordered list of ids: the first is checked first."""

    ids: list[str] = Field(min_length=1, max_length=500)
