"""Files uploaded through the API (such as the organisation logo), stored in the MongoDB ``uploads`` collection.

Each upload is one document keyed by its name, so uploading again replaces the previous file. Until a logo is
uploaded, the default logo in ``modules/settings/assets`` is served."""

from pathlib import Path

from bson import Binary
from fastapi import HTTPException, UploadFile

from app.common.utils import utcnow
from app.core.database import get_db
from app.modules.audit.service import write_audit

IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/svg+xml",
    "image/webp",
    "image/avif",
    "image/gif",
}
MAX_IMAGE_BYTES = 512 * 1024

LOGO = "logo"
DEFAULT_LOGO = Path(__file__).resolve().parent / "assets" / "HOG.avif"


async def save_image(name: str, file: UploadFile, username: str) -> dict:
    if file.content_type not in IMAGE_TYPES:
        raise HTTPException(415, "Upload a PNG, JPEG, SVG, WebP, AVIF or GIF image")
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise HTTPException(422, "The file is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"The image is larger than {MAX_IMAGE_BYTES // 1024} KB")
    doc = {
        "file_name": file.filename,
        "content_type": file.content_type,
        "size": len(data),
        "data": Binary(data),
        "uploaded_by": username,
        "uploaded_at": utcnow(),
    }
    await get_db().uploads.replace_one({"_id": name}, doc, upsert=True)
    info = {k: v for k, v in doc.items() if k != "data"}
    await write_audit("UPLOAD", name, "UPLOAD", username, after=info)
    return info


async def get_upload(name: str) -> dict | None:
    return await get_db().uploads.find_one({"_id": name})


async def upload_info(name: str) -> dict | None:
    return await get_db().uploads.find_one({"_id": name}, {"data": 0})


async def delete_upload(name: str, username: str) -> None:
    before = await upload_info(name)
    if not before:
        raise HTTPException(404, "Nothing has been uploaded")
    await get_db().uploads.delete_one({"_id": name})
    await write_audit("UPLOAD", name, "DELETE", username, before=before)


async def current_logo() -> dict | None:
    """The uploaded logo, else the default one: ``{data, content_type, version, uploaded}``."""
    doc = await get_upload(LOGO)
    if doc:
        return {
            "data": bytes(doc["data"]),
            "content_type": doc["content_type"],
            "version": doc["uploaded_at"].isoformat(),
            "uploaded": True,
        }
    if DEFAULT_LOGO.is_file():
        return {
            "data": DEFAULT_LOGO.read_bytes(),
            "content_type": "image/avif",
            "version": f"default-{int(DEFAULT_LOGO.stat().st_mtime)}",
            "uploaded": False,
        }
    return None
