"""Mounts every business module's routes under /api/v1."""

from fastapi import APIRouter

from app.integrations.netsuite import router as netsuite
from app.modules.api_keys import router as api_keys
from app.modules.audit import router as audit
from app.modules.auth import router as auth
from app.modules.campaigns import router as campaigns
from app.modules.eligibility import router as eligibility
from app.modules.meta import router as meta
from app.modules.pricing import router as pricing
from app.modules.roles import router as roles
from app.modules.sales_orders import router as sales_orders
from app.modules.settings import router as settings
from app.modules.users import router as users

router = APIRouter(prefix="/api/v1")
for module in (
    auth,
    meta,
    users,
    roles,
    settings,
    api_keys,
    campaigns,
    eligibility,
    pricing,
    sales_orders,
    netsuite,
    audit,
):
    router.include_router(module.router)
