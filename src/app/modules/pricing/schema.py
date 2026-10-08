"""The pricing request: the sales order a caller sends to be priced."""

from datetime import date
from decimal import Decimal

from pydantic import Field

from app.modules.sales_orders.schema import CamelModel


# --------------------------------------------------------------------------- pricing request
class PricingCustomerIn(CamelModel):
    id: str = Field(min_length=1, max_length=64)
    name: str | None = None
    customer_group: str | None = None
    channel: str | None = None
    banner: str | None = None
    marketing_flag: str | None = None
    price_level_type: str | None = None
    base_discount_percentage: Decimal | None = Field(None, ge=0, le=1, description="Fraction: 0.20 = 20 %")
    accelerate_eligible: bool | None = None
    monthly_promotion_eligible: bool | None = None
    mts_eligible: bool | None = None
    assigned_price_list_id: str | None = None


class PricingItemIn(CamelModel):
    line_id: str | None = None
    sku: str = Field(min_length=1, max_length=64)
    product_name: str | None = None
    quantity: int = Field(gt=0, le=1_000_000)
    base_price: Decimal | None = Field(None, ge=0)
    current_price: Decimal | None = Field(None, ge=0, description="Price the order currently charges, if any")
    rrp: Decimal | None = Field(None, ge=0)
    item_group: str | None = None
    item_flag: str | None = None
    item_internal_id: str | None = None
    category: str | None = None
    accelerate_flag: bool | None = None
    commodity_flag: bool | None = None
    wholesale_price: Decimal | None = Field(None, ge=0)
    shipper_quantity: Decimal | None = None
    line_type: str | None = None
    exclude_pricing_line: bool = False
    manual_override: bool = False
    existing_manual_rate: Decimal | None = Field(None, ge=0)


class PricingRequestIn(CamelModel):
    sales_order_id: str = Field(min_length=1, max_length=64)
    customer: PricingCustomerIn
    order_date: date | None = Field(None, description="Defaults to today in the business timezone")
    promotion_code: str | None = None
    order_status: str | None = None
    pricing_status: str | None = None
    items: list[PricingItemIn] = Field(min_length=1, max_length=500)
