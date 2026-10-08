"""Campaigns and their rules: configuration, approval and the order in which rules are checked."""

from datetime import date

from fastapi import APIRouter, Response

from app.api.dependencies import CampaignApprover, CampaignWriter, Viewer
from app.modules.campaigns import service as svc
from app.modules.campaigns.schema import CampaignIn, CommentIn, OrderIn, RuleIn

router = APIRouter(tags=["Campaigns"])


@router.get("/campaigns", summary="Campaigns in check order (by type, then priority)")
async def list_campaigns(_: Viewer, q: str | None = None, status: str | None = None):
    return await svc.list_campaigns(q, status)


@router.get("/campaigns/check-order", summary="The rules of the active campaigns, in the order they are checked")
async def check_order(_: Viewer, on: date | None = None):
    return await svc.check_order_view(on)


@router.put("/campaigns/order", summary="Set the campaign priority: the first id is checked first")
async def reorder_campaigns(body: OrderIn, user: CampaignApprover):
    return await svc.reorder_campaigns(body.ids, user["username"])


@router.post("/campaigns", status_code=201)
async def create_campaign(body: CampaignIn, user: CampaignWriter):
    return await svc.create_campaign(body, user["username"])


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, _: Viewer):
    return await svc.get_campaign(campaign_id)


@router.put("/campaigns/{campaign_id}", summary="Change a campaign (a submitted or approved one returns to Draft)")
async def update_campaign(campaign_id: str, body: CampaignIn, user: CampaignWriter):
    return await svc.update_campaign(campaign_id, body, user["username"])


@router.delete("/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: str, user: CampaignWriter):
    await svc.delete_campaign(campaign_id, user["username"])
    return Response(status_code=204)


@router.post("/campaigns/{campaign_id}/submit")
async def submit(campaign_id: str, user: CampaignWriter):
    return await svc.submit(campaign_id, user["username"])


@router.post("/campaigns/{campaign_id}/approve")
async def approve(campaign_id: str, user: CampaignApprover, body: CommentIn | None = None):
    return await svc.approve(campaign_id, user["username"], body.comment if body else None)


@router.post("/campaigns/{campaign_id}/reject", summary="Reject a submitted campaign: it returns to Draft")
async def reject(campaign_id: str, user: CampaignApprover, body: CommentIn | None = None):
    return await svc.reject(campaign_id, user["username"], body.comment if body else None)


@router.post("/campaigns/{campaign_id}/disable")
async def disable(campaign_id: str, user: CampaignApprover):
    return await svc.set_enabled(campaign_id, False, user["username"])


@router.post("/campaigns/{campaign_id}/enable")
async def enable(campaign_id: str, user: CampaignApprover):
    return await svc.set_enabled(campaign_id, True, user["username"])


@router.post("/campaigns/{campaign_id}/rules", status_code=201)
async def add_rule(campaign_id: str, body: RuleIn, user: CampaignWriter):
    return await svc.add_rule(campaign_id, body, user["username"])


@router.put("/campaigns/{campaign_id}/rules/order", summary="Set the rule priority: the first id is checked first")
async def reorder_rules(campaign_id: str, body: OrderIn, user: CampaignWriter):
    return await svc.reorder_rules(campaign_id, body.ids, user["username"])


@router.put("/campaigns/{campaign_id}/rules/{rule_id}")
async def update_rule(campaign_id: str, rule_id: str, body: RuleIn, user: CampaignWriter):
    return await svc.update_rule(campaign_id, rule_id, body, user["username"])


@router.delete("/campaigns/{campaign_id}/rules/{rule_id}")
async def delete_rule(campaign_id: str, rule_id: str, user: CampaignWriter):
    return await svc.delete_rule(campaign_id, rule_id, user["username"])
