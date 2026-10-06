"""Event media upload — primary image, gallery images, videos, documents. See docs/event-media-upload-contract.md."""
import re
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.dependencies import extract_access_token, get_current_user, get_optional_current_user
from app.db.database import get_db
from app.models.enterprise_model import Enterprise
from app.models.event_media_model import EventMedia
from app.repository.event_repo import STAFF_ROLES, is_platform_super_admin, resolve_caller_tenant_id
from app.schemas.event_media_schema import EventMediaAssetResponse, EventMediaDeleteResponse, EventMediaField
from app.services import event_media_service as media

router = APIRouter(tags=["Event Media"])

_ASSET_FILE = re.compile(r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.([A-Za-z0-9]{1,10})$")


def _uploader_scope(db: Session, request: Request, current_user: dict, enterprise_id: UUID | None):
    """(tenant_id, enterprise_id) the upload belongs to, or 403. Same people who may create/edit Events:
    an Enterprise Admin / provider of the tenant, or an active Platform Super Admin naming the enterprise."""
    if is_platform_super_admin(current_user):
        if enterprise_id is None:
            raise HTTPException(422, "enterprise_id is required when uploading as a Platform Super Admin")
        enterprise = db.query(Enterprise).filter(Enterprise.id == enterprise_id, Enterprise.is_deleted.is_(False)).first()
        if enterprise is None or enterprise.tenant_id is None:
            raise HTTPException(404, "Enterprise not found")
        return enterprise.tenant_id, enterprise.id
    if current_user.get("role") not in ("admin", "provider"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not authorized")
    tenant = resolve_caller_tenant_id(db, current_user, access_token=extract_access_token(request))
    if not tenant:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Authenticated tenant identity required")
    if enterprise_id is not None:
        enterprise = db.query(Enterprise).filter(Enterprise.id == enterprise_id, Enterprise.is_deleted.is_(False)).first()
        if enterprise is None or str(enterprise.tenant_id) != str(tenant):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not authorized for this enterprise")
    return uuid.UUID(str(tenant)), enterprise_id


@router.get(
    "/policy",
    summary="Event media limits per field",
    description="Allowed MIME types, extensions, max file size and max file count for each Event media field. Public; use it to configure the uploaders.",
)
def event_media_policy():
    return media.policy_payload()


@router.post(
    "/",
    response_model=EventMediaAssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload one Event media file",
    description=(
        "Single-step multipart upload (storage is this API's own disk, so there is no init/complete handshake): "
        "send one file and the `field` it is for; get back an asset with a hosted https `url`. Put that url in the "
        "Event create/update JSON — referencing it from a saved Event is what completes the upload. Unreferenced "
        "files are deleted 24 h after upload.\n\n"
        "Auth: Enterprise Admin / provider (tenant resolved from the login), or a Platform Super Admin who also sends "
        "`enterprise_id`. The file is owned by that tenant and can only be attached to that tenant's Events.\n\n"
        "Limits per field are in `GET /events/media/policy`. The MIME type, extension and the file's real contents "
        "must all agree. Send the same `client_ref` when retrying a request whose outcome you did not see: the "
        "original asset is returned (HTTP 200) instead of storing a duplicate."
    ),
)
def upload_event_media(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="The file."),
    field: EventMediaField = Form(..., description="primary_image | gallery_images | videos | documents"),
    enterprise_id: UUID | None = Form(None, description="Required for a Platform Super Admin; optional for tenant staff."),
    client_ref: str | None = Form(None, max_length=64, description="Your own unique id for this upload attempt (retry key)."),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    tenant_id, enterprise = _uploader_scope(db, request, current_user, enterprise_id)
    asset, created = media.store_upload(
        db, file=file, field=field.value, tenant_id=tenant_id, enterprise_id=enterprise,
        uploaded_by=str(current_user.get("id")) if current_user.get("id") else None, client_ref=client_ref,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return media.asset_dict(asset)


@router.get(
    "/{asset_file}",
    summary="Download / display an Event media file",
    description=(
        "`{asset_file}` is `<asset id>.<ext>` — exactly the tail of the `url` the upload returned.\n\n"
        "- **Public, no Authorization:** an image used as the primary image or in the gallery of a *published* Event "
        "(cacheable; plain `<img>` / mobile image URL works).\n"
        "- **Any logged-in user:** any other file (video, document) of a published Event.\n"
        "- **Owning tenant's staff and active Platform Super Admins:** every file of their Events, including drafts and "
        "events awaiting approval, and uploads not yet attached.\n"
        "Documents download with their original file name; images and videos are served inline."
    ),
)
def get_event_media(
    request: Request,
    asset_file: str = Path(..., description="<asset id>.<ext>"),
    db: Session = Depends(get_db),
    current_user: dict | None = Depends(get_optional_current_user),
):
    match = _ASSET_FILE.match(asset_file)
    if not match:
        raise HTTPException(404, "File not found")
    asset = db.get(EventMedia, uuid.UUID(match.group(1)))
    if asset is None or asset.ext != match.group(2).lower() or not media.asset_path(asset).is_file():
        raise HTTPException(404, "File not found")

    headers = {"X-Content-Type-Options": "nosniff"}
    if asset.mime_type.startswith("image/") and media.is_published_cover(db, asset.id):
        headers["Cache-Control"] = "public, max-age=86400"
        return FileResponse(media.asset_path(asset), media_type=asset.mime_type, headers=headers)

    if current_user is None:
        raise HTTPException(401, "Not authenticated")
    allowed = media.is_published_reference(db, asset.id)
    if not allowed and is_platform_super_admin(current_user):
        allowed = True
    if not allowed and current_user.get("role") in STAFF_ROLES:
        tenant = resolve_caller_tenant_id(db, current_user, access_token=extract_access_token(request))
        allowed = bool(tenant) and str(tenant) == str(asset.tenant_id)
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not authorized for this file")

    filename = asset.original_name if asset.field == "documents" else None
    return FileResponse(media.asset_path(asset), media_type=asset.mime_type, filename=filename, headers=headers)


@router.delete(
    "/{asset_id}",
    response_model=EventMediaDeleteResponse,
    summary="Discard an upload that is not used by any Event",
    description=(
        "Removes a file you uploaded but have not saved into an Event (for example the user removed it from the "
        "form before saving). A file an Event still references answers 409 — remove it from the Event with an "
        "update; the file is then deleted automatically 24 h later. Same tenant/ownership rule as upload."
    ),
)
def delete_event_media(
    request: Request,
    asset_id: UUID = Path(..., description="Asset id from the upload response."),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    asset = db.get(EventMedia, asset_id)
    if asset is None:
        raise HTTPException(404, "File not found")
    if not is_platform_super_admin(current_user):
        if current_user.get("role") not in ("admin", "provider"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not authorized")
        tenant = resolve_caller_tenant_id(db, current_user, access_token=extract_access_token(request))
        if not tenant or str(tenant) != str(asset.tenant_id):
            raise HTTPException(404, "File not found")  # not another tenant's business that it exists
    if media.is_referenced(db, asset.id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This file is used by an Event; remove it from the Event first")
    media.asset_path(asset).unlink(missing_ok=True)
    db.delete(asset)
    db.commit()
    return {"deleted": True}
