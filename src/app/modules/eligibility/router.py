"""Programme eligibility records (Accelerate, Make the Switch, Monthly Promotion)."""

from fastapi import APIRouter

from app.api.dependencies import CampaignWriter, Viewer
from app.modules.eligibility import service
from app.modules.eligibility.schema import EligibilityIn

router = APIRouter(tags=["Programme eligibility"])


@router.get("/programme-eligibility")
async def list_records(_: Viewer, programme: str | None = None):
    return await service.list_records(programme)


@router.post("/programme-eligibility", status_code=201)
async def create_record(body: EligibilityIn, user: CampaignWriter):
    return await service.create(body, user["username"])


@router.put("/programme-eligibility/{eligibility_id}")
async def update_record(eligibility_id: str, body: EligibilityIn, user: CampaignWriter):
    return await service.update(eligibility_id, body, user["username"])
