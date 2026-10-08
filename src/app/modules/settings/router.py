"""Business settings: organisation name, currency, business timezone and the organisation logo."""

from zoneinfo import available_timezones

from fastapi import APIRouter, HTTPException, Response, UploadFile

from app.api.dependencies import CurrentUser, UserAdmin
from app.common.utils import serialize, utcnow
from app.core import app_settings
from app.core.database import get_db
from app.modules.audit.service import write_audit
from app.modules.settings import service as uploads
from app.modules.settings.schema import AppSettingsUpdate

router = APIRouter(tags=["Settings"])


@router.get("/settings")
async def get_settings(_: CurrentUser):
    doc = await app_settings.refresh(force=True)
    if not doc:
        raise HTTPException(404, "The application has not been set up")
    return serialize(doc)


@router.put("/settings")
async def update_settings(body: AppSettingsUpdate, admin: UserAdmin):
    db = get_db()
    before = await db.app_settings.find_one({"_id": app_settings.SETTINGS_ID})
    if not before:
        raise HTTPException(404, "The application has not been set up")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "timezone" in changes and changes["timezone"] not in available_timezones():
        raise HTTPException(422, f"Unknown timezone '{changes['timezone']}'")
    if "currency" in changes:
        changes["currency"] = changes["currency"].upper()
    changes.update(updated_at=utcnow(), updated_by=admin["username"])
    await db.app_settings.update_one({"_id": app_settings.SETTINGS_ID}, {"$set": changes})
    after = await app_settings.refresh(force=True)
    await write_audit("SETTINGS", app_settings.SETTINGS_ID, "UPDATE", admin["username"], before, after)
    return serialize(after)


@router.get("/settings/logo", summary="The organisation logo, or the default one (public: shown on the sign-in page)")
async def get_logo():
    logo = await uploads.current_logo()
    if not logo:
        raise HTTPException(404, "No logo")
    return Response(
        content=logo["data"],
        media_type=logo["content_type"],
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            # An uploaded SVG opened directly must not run scripts.
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )


@router.put("/settings/logo", summary="Upload or replace the organisation logo (image, max 512 KB)")
async def upload_logo(file: UploadFile, admin: UserAdmin):
    return serialize(await uploads.save_image(uploads.LOGO, file, admin["username"]))


@router.delete("/settings/logo", status_code=204)
async def delete_logo(admin: UserAdmin):
    await uploads.delete_upload(uploads.LOGO, admin["username"])
