"""Sales orders: record a normalised order, list stored orders, view one and price it again."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import OrderWriter, Viewer, get_order_principal
from app.common.pagination import Page, PageSize
from app.modules.sales_orders import service as so
from app.modules.sales_orders.schema import CancellationIn, NormalisedSalesOrder

router = APIRouter(tags=["Sales orders"])


Principal = Annotated[dict, Depends(get_order_principal)]


@router.post(
    "/sales-orders",
    summary="Record and price a normalised sales order",
    description=(
        "Source-independent sales order: `salesOrder`, `customer` (id, channel, banner) and `lines` "
        "(line id, item code, quantity, current and base unit price, RRP, item flag, shipper quantity). "
        "Authenticate with `X-API-Key` or a user token."
    ),
)
async def receive_normalised(order: NormalisedSalesOrder, principal: Principal):
    return await so.receive(order, principal)


# --------------------------------------------------------------------------- saved sales orders
@router.get("/sales-orders", summary="List received sales orders")
async def list_sales_orders(
    _: Viewer,
    q: str | None = None,
    status: str | None = None,
    channel: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: Page = 1,
    page_size: PageSize = 25,
):
    return await so.list_sales_orders(q, status, channel, date_from, date_to, page, page_size)


@router.get(
    "/sales-orders/{sales_order_id}", summary="A sales order with its lines, pricing and the JSON it was received as"
)
async def get_sales_order(sales_order_id: str, _: Viewer, source: str | None = None):
    return await so.get_sales_order(sales_order_id, source)


@router.post(
    "/sales-orders/{sales_order_id}/evaluate", summary="Price a stored sales order again with today's campaigns"
)
async def evaluate_stored(sales_order_id: str, user: OrderWriter, source: str | None = None):
    return await so.evaluate_stored(sales_order_id, source, user["username"])


@router.post(
    "/sales-orders/cancel",
    summary="Cancellation update: mark a sales order cancelled",
    description=(
        "`salesOrderId`, optional `sourceSystem`, `pricingStatus` (CANCELLED), `latestPricingRequestId`, "
        "`cancelledAt`. The order is marked cancelled; its pricing history is kept and it is not priced again. "
        "Authenticate with `X-API-Key` or a user token."
    ),
)
async def cancel_sales_order(body: CancellationIn, principal: Principal):
    return await so.cancel(body, principal)


@router.get("/sales-orders/{sales_order_id}/pricing-history", summary="Every pricing transaction of a sales order")
async def pricing_history(sales_order_id: str, _: Viewer, source: str | None = None):
    return await so.pricing_history(sales_order_id, source)
