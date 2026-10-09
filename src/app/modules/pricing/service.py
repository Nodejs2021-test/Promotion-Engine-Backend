"""Pricing service: a normalised sales order -> the rules engine -> the pricing result.

It loads the approved campaigns from MongoDB and hands them, with the order, to the pure rules engine
(``app/modules/pricing``). The NetSuite integration and the pricing API both come through here, so they always
price the same way.
"""

from decimal import Decimal

from app.common.utils import serialize, today, utcnow
from app.core import app_settings
from app.core.database import get_db
from app.modules.campaigns.service import load_engine_campaigns
from app.modules.eligibility.service import load_engine_records
from app.modules.pricing.conditions import to_bool
from app.modules.pricing.engine import evaluate_sales_order
from app.modules.pricing.model import Order, OrderCustomer, OrderLine
from app.modules.pricing.schema import PricingRequestIn
from app.modules.sales_orders.schema import NormalisedSalesOrder


def _dec(v) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


def engine_order(order: NormalisedSalesOrder) -> Order:
    c, h = order.customer, order.sales_order
    return Order(
        sales_order_id=h.sales_order_id,
        order_date=order.order_date or today(),
        currency=app_settings.currency() or "",
        promotion_code=h.promotion_code or (h.coupon_codes[0] if h.coupon_codes else None),
        order_status=h.order_status,
        pricing_status=h.pricing_status,
        customer=OrderCustomer(
            customer_id=c.customer_id,
            name=c.customer_name,
            customer_group=c.customer_group,
            channel=c.channel,
            banner=c.banner,
            marketing_flag=c.marketing_flag,
            price_level_type=c.price_level_type,
            base_discount_percentage=_dec(c.base_discount_percentage),
            accelerate_eligible=to_bool(c.accelerate_eligible),
            monthly_promotion_eligible=to_bool(c.monthly_promotion_eligible),
            mts_eligible=to_bool(c.mts_eligible),
            assigned_price_list_id=c.assigned_price_list_id,
        ),
        lines=tuple(
            OrderLine(
                line_id=ln.line_id,
                sku=ln.item_code,
                quantity=ln.quantity,
                base_price=_dec(ln.base_unit_price),
                current_price=_dec(ln.current_unit_price),
                product_name=ln.item_name,
                item_group=ln.item_group,
                item_flag=ln.item_flag,
                item_id=ln.item_id,
                category=ln.category,
                accelerate_flag=ln.accelerate_flag,
                commodity_flag=ln.commodity_flag,
                wholesale_price=_dec(ln.wholesale_price),
                rrp=_dec(ln.rrp),
                shipper_quantity=_dec(ln.shipper_quantity),
                line_type=ln.line_type,
                exclude_pricing_line=ln.exclude_pricing_line,
                manual_override=ln.manual_override,
                existing_manual_rate=_dec(ln.existing_manual_rate),
            )
            for ln in order.lines
        ),
    )


async def code_usage(campaigns, order: NormalisedSalesOrder) -> list[tuple[str, int, int]]:
    """(rule id, orders it was applied to, of which this customer's) for limited promotion-code rules; the order
    being priced and cancelled orders are not counted."""
    out = []
    db = get_db()
    for c in campaigns:
        for r in c.rules:
            if r.family == "PROMOTION_CODE" and (r.max_uses_total or r.max_uses_per_customer):
                query = {
                    "applied_rule_ids": r.rule_id,
                    "cancelled": {"$ne": True},
                    "sales_order_id": {"$ne": order.sales_order.sales_order_id},
                }
                total = await db.sales_orders.count_documents(query)
                mine = await db.sales_orders.count_documents({**query, "customer_id": order.customer.customer_id})
                out.append((r.rule_id, total, mine))
    return out


async def price(order: NormalisedSalesOrder) -> dict:
    """The pricing result of the order (camelCase, JSON ready). Nothing is stored here."""
    campaigns = await load_engine_campaigns()
    result = evaluate_sales_order(
        engine_order(order),
        campaigns,
        app_settings.controlled_values(),
        eligibility=await load_engine_records(),
        usage=await code_usage(campaigns, order),
    )
    return serialize({**result, "evaluatedAt": utcnow().isoformat()})


def from_pricing_request(body: PricingRequestIn) -> NormalisedSalesOrder:
    """The pricing API's request (customer + items) as a normalised sales order."""
    return NormalisedSalesOrder.model_validate(
        {
            "salesOrder": {
                "salesOrderId": body.sales_order_id,
                "source": "API",
                "promotionCode": body.promotion_code,
                "orderStatus": body.order_status,
                "pricingStatus": body.pricing_status,
            },
            "customer": {
                "customerId": body.customer.id,
                "customerName": body.customer.name,
                **body.customer.model_dump(by_alias=True, exclude={"id", "name"}),
            },
            "orderDate": body.order_date,
            "lines": [
                {
                    "lineId": it.line_id or str(i),
                    "itemCode": it.sku,
                    "itemName": it.product_name,
                    "quantity": it.quantity,
                    "baseUnitPrice": it.base_price,
                    "currentUnitPrice": it.current_price,
                    "rrp": it.rrp,
                    "itemGroup": it.item_group,
                    "itemFlag": it.item_flag,
                    "itemId": it.item_internal_id,
                    "category": it.category,
                    "accelerateFlag": it.accelerate_flag,
                    "commodityFlag": it.commodity_flag,
                    "wholesalePrice": it.wholesale_price,
                    "shipperQuantity": it.shipper_quantity,
                    "lineType": it.line_type,
                    "excludePricingLine": it.exclude_pricing_line,
                    "manualOverride": it.manual_override,
                    "existingManualRate": it.existing_manual_rate,
                }
                for i, it in enumerate(body.items, start=1)
            ],
        }
    )
