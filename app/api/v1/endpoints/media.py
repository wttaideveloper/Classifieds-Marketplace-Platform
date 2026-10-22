from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.dependencies import extract_access_token, get_current_user
from app.db.database import get_db
from app.schemas.ebook_schema import MediaAssetOut
from app.services import media_service as svc

router = APIRouter(tags=["Media"])


@router.get("/{asset_id}", response_model=MediaAssetOut, summary="About an uploaded image",
            description="Size, type, pixel dimensions, checksum, virus-scan status, and which eBooks use it. For the business's staff and Super Admins.")
def get_media(request: Request, asset_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    asset = svc.get_asset(db, asset_id)
    if not svc.is_staff_of(db, user, asset, extract_access_token(request)):
        raise HTTPException(status_code=404, detail="Image not found")       # a learner may fetch the file, not its details
    return {**svc.asset_summary(asset), "used_by": svc.references(db, asset)}


@router.get("/{asset_id}/file", summary="The image itself",
            description="Sign-in needed. Served to the business's staff, to Super Admins, and to learners enrolled in a training whose published eBook uses it. "
                        "Anyone else gets 404.", responses={200: {"content": {"image/*": {}}}})
def get_media_file(request: Request, asset_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    asset = svc.get_asset(db, asset_id)
    svc.assert_can_view_file(db, user, asset, extract_access_token(request))
    return FileResponse(path=str(svc.file_path(asset)), media_type=asset.content_type,
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})


@router.delete("/{asset_id}", summary="Delete an uploaded image",
               description="The business's staff only. Refused with 409 `ASSET_IN_USE` (and the list of eBooks) while a draft or published eBook still uses it. "
                           "To replace a picture, upload the new one, point the eBook's image block at its `asset_id`, then delete the old one.")
def delete_media(request: Request, asset_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    asset = svc.get_asset(db, asset_id)
    svc.delete_asset(db, user, asset, extract_access_token(request))
    return {"message": "Image deleted"}
