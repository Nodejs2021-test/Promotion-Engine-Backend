"""NetSuite adapter: receive or preview a NetSuite sales order as-is, and the editable field mapping."""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends

from app.api.dependencies import OrderWriter, UserAdmin, Viewer, get_order_principal
from app.integrations.netsuite import mapping
from app.modules.pricing import service as order_pricing
from app.modules.sales_orders import service as so

router = APIRouter(tags=["NetSuite"])


RawOrder = Annotated[dict[str, Any], Body(description="The sales order exactly as NetSuite sends it")]


Principal = Annotated[dict, Depends(get_order_principal)]


@router.post(
    "/integrations/netsuite/sales-orders",
    summary="Record and price a NetSuite sales order",
    description=(
        "Accepts the NetSuite (SuiteCommerce cart) sales order as-is, maps it with the NetSuite mapping profile, "
        "prices it against the approved campaigns, stores it and returns the pricing result. A payload that "
        "cannot be mapped returns 422 `MAPPING_ERROR`."
    ),
)
async def receive_netsuite(payload: RawOrder, principal: Principal):
    order = await so.normalise("NETSUITE", payload)
    return await so.receive(order, principal, raw=payload)


@router.post("/integrations/netsuite/sales-orders/preview", summary="Map and price a NetSuite order without saving it")
async def preview_netsuite(payload: RawOrder, _: OrderWriter):
    order = await so.normalise("NETSUITE", payload)
    return {
        "normalised": order.model_dump(mode="json", by_alias=True),
        "pricing": await order_pricing.price(order),
    }


@router.get("/integrations/{source}/mapping")
async def get_mapping(source: str, _: Viewer):
    profile, customised = await mapping.get_profile(source)
    return {
        "source": source.upper(),
        "customised": customised,
        "profile": profile,
        "default": mapping.DEFAULT_PROFILES[source.upper()],
    }


@router.put("/integrations/{source}/mapping")
async def put_mapping(source: str, profile: Annotated[dict[str, Any], Body()], admin: UserAdmin):
    saved = await mapping.save_profile(source, profile, admin["username"])
    return {"source": source.upper(), "customised": True, "profile": saved}


@router.delete("/integrations/{source}/mapping")
async def reset_mapping(source: str, admin: UserAdmin):
    profile = await mapping.reset_profile(source, admin["username"])
    return {"source": source.upper(), "customised": False, "profile": profile}
