"""Normalised sales order: the source-independent form in which every sales order is stored.

External payloads (NetSuite, Dataverse) are converted to this structure by the integration layer, so the rest of
the application never depends on raw source field names.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


SalesOrderSource = Literal["NETSUITE", "DATAVERSE", "API", "OTHER"]


def _text(v: Any) -> Any:
    return None if v is None else str(v)


class SalesOrderHeader(CamelModel):
    sales_order_id: str = Field(min_length=1, max_length=64)
    status: str | None = None
    is_new: bool | None = None
    profile_id: str | None = None
    store_integration_id: str | None = None
    source: SalesOrderSource = "API"
    coupon_codes: list[str] = []
    promotion_code: str | None = Field(None, max_length=40, description="Header promotion code")
    order_status: str | None = Field(None, description="e.g. PENDING_FULFILMENT")
    pricing_status: str | None = Field(None, description="e.g. READY_TO_SEND")

    _as_text = field_validator("sales_order_id", "profile_id", "store_integration_id", "status", mode="before")(
        lambda v: _text(v)
    )


class SalesOrderCustomer(CamelModel):
    """Customer identity plus the attributes the order carries."""

    customer_id: str = Field(min_length=1, max_length=64)
    customer_name: str | None = None
    channel: str | None = None
    banner: str | None = None
    customer_group: str | None = None
    marketing_flag: str | None = None
    price_level_type: str | None = Field(None, description="PERCENTAGE, WHOLESALE, RRP, PRICE_LIST, BYPASS_100_PERCENT")
    base_discount_percentage: Decimal | None = Field(None, ge=0, le=1, description="Fraction: 0.20 = 20 %")
    accelerate_eligible: bool | None = None
    monthly_promotion_eligible: bool | None = None
    mts_eligible: bool | None = None
    assigned_price_list_id: str | None = None
    attributes: dict[str, str] = {}

    _as_text = field_validator(
        "customer_id",
        "customer_name",
        "customer_group",
        "channel",
        "banner",
        "marketing_flag",
        "assigned_price_list_id",
        mode="before",
    )(lambda v: _text(v))

    @field_validator("price_level_type", mode="before")
    @classmethod
    def _level(cls, v: Any) -> Any:
        return str(v).strip().upper().replace(" ", "_") or None if v is not None else None


class SalesOrderLine(CamelModel):
    line_id: str = Field(min_length=1, max_length=64)
    item_code: str = Field(min_length=1, max_length=64, description="SKU / item code")
    item_id: str | None = Field(None, description="Item id in the source system (e.g. NetSuite internal id)")
    item_name: str | None = None
    quantity: int = Field(gt=0, le=1_000_000)
    remaining_quantity: int | None = None
    returned_quantity: int | None = None
    current_unit_price: Decimal | None = Field(None, ge=0, description="Price the source system currently charges")
    base_unit_price: Decimal | None = Field(None, ge=0, description="Base (list) price of the item")
    rrp: Decimal | None = Field(None, ge=0)
    item_flag: str | None = None
    item_group: str | None = None
    category: str | None = None
    accelerate_flag: bool | None = None
    commodity_flag: bool | None = None
    wholesale_price: Decimal | None = Field(None, ge=0)
    shipper_quantity: Decimal | None = None
    line_type: str = Field("CHARGEABLE_ITEM", description="CHARGEABLE_ITEM, FREE_STOCK, NOTE, MARKETING_POS, ...")
    exclude_pricing_line: bool = False
    manual_override: bool = False
    existing_manual_rate: Decimal | None = Field(None, ge=0)
    attributes: dict[str, Any] = {}

    _as_text = field_validator("line_id", "item_code", "item_id", "item_flag", "item_group", "category", mode="before")(
        lambda v: _text(v)
    )

    @field_validator("line_type", mode="before")
    @classmethod
    def _line_type(cls, v: Any) -> Any:
        return str(v).strip().upper().replace(" ", "_") if v not in (None, "") else "CHARGEABLE_ITEM"


class NormalisedSalesOrder(CamelModel):
    sales_order: SalesOrderHeader
    customer: SalesOrderCustomer
    order_date: date | None = Field(None, description="Defaults to today in the business timezone")
    lines: list[SalesOrderLine] = Field(min_length=1, max_length=500)

    @field_validator("lines")
    @classmethod
    def _unique_lines(cls, lines: list[SalesOrderLine]) -> list[SalesOrderLine]:
        seen: set[str] = set()
        dupes = sorted({ln.line_id for ln in lines if ln.line_id in seen or seen.add(ln.line_id)})
        if dupes:
            raise ValueError(f"Duplicate line id(s): {', '.join(dupes)}")
        return lines
