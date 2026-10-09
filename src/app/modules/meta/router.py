"""Reference metadata for the UI.

Every option list the frontend shows (permissions, timezones, campaign statuses, rule condition fields,
operators and actions) comes from this endpoint, so the UI holds no hardcoded domain lists.
"""

from zoneinfo import available_timezones

from fastapi import APIRouter

from app.core import app_settings
from app.core.permissions import PERMISSIONS
from app.modules.campaigns.service import STATUSES
from app.modules.pricing import model

router = APIRouter(tags=["Metadata"])


def _opts(d: dict[str, str]) -> list[dict]:
    return [{"value": k, "label": v} for k, v in d.items()]


def _fields(d: dict[str, str], group: str) -> list[dict]:
    return [{"value": k, "label": v, "group": group, "operators": list(model.FIELD_OPERATORS[k])} for k, v in d.items()]


@router.get("/meta")
async def meta():
    return {
        "permissions": [{"value": k, "label": label, "description": d} for k, (label, d) in PERMISSIONS.items()],
        "timezones": sorted(available_timezones()),
        "campaign_statuses": _opts(STATUSES),
        "condition_fields": [
            *_fields(model.CUSTOMER_FIELDS, "Customer"),
            *_fields(model.PRODUCT_FIELDS, "Product"),
            *_fields(model.ORDER_FIELDS, "Order"),
        ],
        "operators": _opts({**model.TEXT_OPERATORS, **model.NUMBER_OPERATORS}),
        "action_types": _opts(model.ACTION_TYPES),
        "rule_families": _opts(model.RULE_FAMILIES),
        "rule_types": _opts(model.RULE_TYPES),
        "comparison_modes": _opts(model.COMPARISON_MODES),
        "cap_treatments": _opts(model.CAP_TREATMENTS),
        "quantity_bases": _opts(model.QUANTITY_BASES),
        "item_roles": _opts(model.ITEM_ROLES),
        "programmes": _opts(model.PROGRAMMES),
        "eligibility_scopes": _opts(model.ELIGIBILITY_SCOPES),
        "eligibility_modes": _opts(model.ELIGIBILITY_MODES),
        "boolean_fields": sorted(model.BOOLEAN_FIELDS),
        "controlled_values": app_settings.controlled_values(),
    }
