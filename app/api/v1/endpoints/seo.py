from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.seo_schema import CanonicalUrlCreate, SeoApiResponse, SeoMetaCreate, SeoMetaUpdate
from app.services.seo_service import (
    create_seo_meta_service,
    generate_robots_txt_service,
    generate_sitemap_xml_service,
    get_seo_meta_service,
    set_canonical_url_service,
    update_seo_meta_service,
)

router = APIRouter()


@router.post(
    "/meta",
    response_model=SeoApiResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_user)],
)
def create_seo_meta(payload: SeoMetaCreate, request: Request, db: Session = Depends(get_db)):
    return create_seo_meta_service(db=db, payload=payload, base_url=str(request.base_url))


@router.get("/meta/{entity_id}", response_model=SeoApiResponse, status_code=status.HTTP_200_OK)
def get_seo_meta(entity_id: str, request: Request, db: Session = Depends(get_db)):
    return get_seo_meta_service(db=db, entity_id=entity_id, base_url=str(request.base_url))


@router.put(
    "/meta/{seo_id}",
    response_model=SeoApiResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(get_current_user)],
)
def update_seo_meta(
    seo_id: str,
    payload: SeoMetaUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    return update_seo_meta_service(db=db, seo_id=seo_id, payload=payload, base_url=str(request.base_url))


@router.get("/sitemap.xml", status_code=status.HTTP_200_OK)
def get_sitemap_xml(db: Session = Depends(get_db)):
    return Response(content=generate_sitemap_xml_service(db), media_type="application/xml")


@router.get("/robots.txt", status_code=status.HTTP_200_OK)
def get_robots_txt(request: Request):
    return PlainTextResponse(generate_robots_txt_service(str(request.base_url)))


@router.post(
    "/canonical",
    response_model=SeoApiResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_user)],
)
def set_canonical_url(
    payload: CanonicalUrlCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    return set_canonical_url_service(db=db, payload=payload, base_url=str(request.base_url))
