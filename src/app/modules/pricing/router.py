"""Pricing API: price a customer and items against the approved campaigns."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_order_principal
from app.modules.pricing import service as order_pricing
from app.modules.pricing.schema import PricingRequestIn
from app.modules.sales_orders import service as so

router = APIRouter(tags=["Pricing"])


Principal = Annotated[dict, Depends(get_order_principal)]


@router.post(
    "/pricing/evaluate",
    summary="Price a sales order against the approved campaigns",
    description=(
        "`salesOrderId`, `customer` (`id`, `name`, `customerGroup`, `channel`), `orderDate` (default today) and "
        "`items` (`sku`, `productName`, `quantity`, `basePrice`, `currentPrice`, `itemGroup`, `itemFlag`). Returns "
        "the final price of every line with the campaign and rule applied and the explanation. The order is stored "
        "in Sales Orders (source API). Authenticate with `X-API-Key` or a user token."
    ),
)
async def evaluate_pricing(body: PricingRequestIn, principal: Principal):
    return await so.receive(order_pricing.from_pricing_request(body), principal)
