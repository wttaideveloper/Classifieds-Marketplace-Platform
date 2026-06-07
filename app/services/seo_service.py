import re
import uuid
from decimal import Decimal
from urllib.parse import urljoin
from xml.sax.saxutils import escape

from fastapi import status
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.exceptions.custom_exception import CustomException
from app.models.admin_model import Business
from app.models.blog_model import Blog
from app.models.merchant_model import MerchantListing
from app.models.seo_model import SeoMetadata, SitemapChangeFrequency, SitemapEntry
from app.repository.seo_repo import (
    get_seo_by_entity,
    get_seo_by_id,
    get_seo_by_slug,
    get_sitemap_entry_by_entity,
    list_sitemap_entries,
    save_seo,
    update_seo,
    upsert_sitemap_entry,
)


def _to_uuid(value: str, field_name: str):
    try:
        return uuid.UUID(str(value))
    except Exception:
        raise CustomException(400, f"Invalid {field_name}")


def _normalize_slug(slug: str) -> str:
    value = (slug or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    value = re.sub(r"[\s-]+", "-", value).strip("-")
    if not value:
        raise CustomException(400, "slug is required")
    return value


def _entity_exists(db: Session, entity_type: str, entity_id) -> bool:
    if entity_type == "Business":
        return (
            db.query(Business)
            .filter(Business.id == entity_id, Business.is_deleted == False)
            .first()
            is not None
        )
    if entity_type == "Listing":
        return db.query(MerchantListing).filter(MerchantListing.id == entity_id).first() is not None
    if entity_type == "Blog":
        return db.query(Blog).filter(Blog.id == entity_id).first() is not None
    return False


def _entity_path(entity_type: str, slug: str) -> str:
    paths = {
        "Business": f"businesses/{slug}",
        "Listing": f"listings/{slug}",
        "Blog": f"blogs/{slug}",
    }
    return paths[entity_type]


def _sitemap_url(base_url: str, entity_type: str, slug: str) -> str:
    base = str(base_url).replace("/api/v1/", "/").rstrip("/") + "/"
    return urljoin(base, _entity_path(entity_type, slug))


def _priority_for(entity_type: str) -> Decimal:
    if entity_type == "Business":
        return Decimal("0.8")
    if entity_type == "Listing":
        return Decimal("0.7")
    return Decimal("0.6")


def _frequency_for(entity_type: str):
    if entity_type == "Listing":
        return SitemapChangeFrequency.Daily
    if entity_type == "Business":
        return SitemapChangeFrequency.Weekly
    return SitemapChangeFrequency.Monthly


def _ensure_unique_slug(db: Session, slug: str, current_id=None):
    existing = get_seo_by_slug(db, slug)
    if existing and str(existing.id) != str(current_id):
        raise CustomException(400, "slug already exists")


def _sync_sitemap_entry(db: Session, seo: SeoMetadata, base_url: str):
    sitemap_url = _sitemap_url(base_url, seo.entity_type.value, seo.slug)
    entry = get_sitemap_entry_by_entity(db, seo.entity_type, seo.entity_id)
    if not entry:
        entry = SitemapEntry(
            entity_type=seo.entity_type,
            entity_id=seo.entity_id,
            sitemap_url=sitemap_url,
            priority=_priority_for(seo.entity_type.value),
            change_frequency=_frequency_for(seo.entity_type.value),
        )
        return upsert_sitemap_entry(db, entry)

    entry.sitemap_url = sitemap_url
    entry.last_modified = func.now()
    entry.priority = _priority_for(seo.entity_type.value)
    entry.change_frequency = _frequency_for(seo.entity_type.value)
    return upsert_sitemap_entry(db, entry)


def _seo_to_dict(seo: SeoMetadata, sitemap_url: str):
    return {
        "seo_id": str(seo.id),
        "entity_type": seo.entity_type.value,
        "entity_id": str(seo.entity_id),
        "meta_title": seo.meta_title,
        "meta_description": seo.meta_description,
        "meta_keywords": seo.meta_keywords,
        "canonical_url": seo.canonical_url,
        "og_title": seo.og_title,
        "og_description": seo.og_description,
        "og_image": seo.og_image,
        "slug": seo.slug,
        "sitemap_url": sitemap_url,
        "created_at": seo.created_at,
        "updated_at": seo.updated_at,
    }


def create_seo_meta_service(db: Session, payload, base_url: str):
    try:
        entity_id = _to_uuid(payload.entity_id, "entity_id")
        entity_type = payload.entity_type.value
        if not _entity_exists(db, entity_type, entity_id):
            raise CustomException(404, "entity_id does not exist")

        slug = _normalize_slug(payload.slug)
        _ensure_unique_slug(db, slug)
        if get_seo_by_entity(db, entity_type, entity_id):
            raise CustomException(400, "SEO metadata already exists for entity")

        seo = SeoMetadata(
            entity_type=entity_type,
            entity_id=entity_id,
            meta_title=payload.meta_title,
            meta_description=payload.meta_description,
            meta_keywords=payload.meta_keywords,
            canonical_url=str(payload.canonical_url) if payload.canonical_url else None,
            og_title=payload.og_title or payload.meta_title,
            og_description=payload.og_description or payload.meta_description,
            og_image=str(payload.og_image) if payload.og_image else None,
            slug=slug,
        )
        seo = save_seo(db, seo)
        sitemap_entry = _sync_sitemap_entry(db, seo, base_url)
        return {
            "success": True,
            "message": "SEO metadata saved successfully",
            "data": _seo_to_dict(seo, sitemap_entry.sitemap_url),
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_seo_meta_service(db: Session, entity_id: str, base_url: str):
    try:
        entity_uuid = _to_uuid(entity_id, "entity_id")
        seo = db.query(SeoMetadata).filter(SeoMetadata.entity_id == entity_uuid).first()
        if not seo:
            raise CustomException(404, "SEO metadata not found")
        sitemap_entry = _sync_sitemap_entry(db, seo, base_url)
        return {
            "success": True,
            "message": "SEO metadata fetched successfully",
            "data": _seo_to_dict(seo, sitemap_entry.sitemap_url),
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def update_seo_meta_service(db: Session, seo_id: str, payload, base_url: str):
    try:
        seo_uuid = _to_uuid(seo_id, "seo_id")
        seo = get_seo_by_id(db, seo_uuid)
        if not seo:
            raise CustomException(404, "SEO metadata not found")

        if payload.slug is not None:
            slug = _normalize_slug(payload.slug)
            _ensure_unique_slug(db, slug, current_id=seo.id)
            seo.slug = slug
        for field in (
            "meta_title",
            "meta_description",
            "meta_keywords",
            "og_title",
            "og_description",
        ):
            value = getattr(payload, field, None)
            if value is not None:
                setattr(seo, field, value)
        if payload.canonical_url is not None:
            seo.canonical_url = str(payload.canonical_url)
        if payload.og_image is not None:
            seo.og_image = str(payload.og_image)

        seo = update_seo(db, seo)
        sitemap_entry = _sync_sitemap_entry(db, seo, base_url)
        return {
            "success": True,
            "message": "SEO metadata updated successfully",
            "data": _seo_to_dict(seo, sitemap_entry.sitemap_url),
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def set_canonical_url_service(db: Session, payload, base_url: str):
    try:
        entity_id = _to_uuid(payload.entity_id, "entity_id")
        entity_type = payload.entity_type.value
        if not _entity_exists(db, entity_type, entity_id):
            raise CustomException(404, "entity_id does not exist")

        seo = get_seo_by_entity(db, entity_type, entity_id)
        if not seo:
            raise CustomException(404, "SEO metadata not found")
        seo.canonical_url = str(payload.canonical_url)
        seo = update_seo(db, seo)
        sitemap_entry = _sync_sitemap_entry(db, seo, base_url)
        return {
            "success": True,
            "message": "Canonical URL saved successfully",
            "data": _seo_to_dict(seo, sitemap_entry.sitemap_url),
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def generate_sitemap_xml_service(db: Session):
    entries = list_sitemap_entries(db)
    items = []
    for entry in entries:
        if not _entity_exists(db, entry.entity_type.value, entry.entity_id):
            continue
        last_modified = entry.last_modified.isoformat() if entry.last_modified else ""
        items.append(
            "  <url>"
            f"<loc>{escape(entry.sitemap_url)}</loc>"
            f"<lastmod>{escape(last_modified)}</lastmod>"
            f"<changefreq>{entry.change_frequency.value.lower()}</changefreq>"
            f"<priority>{entry.priority}</priority>"
            "</url>"
        )
    body = "\n".join(items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</urlset>"
    )


def generate_robots_txt_service(base_url: str):
    base = str(base_url).replace("/api/v1/", "/").rstrip("/")
    return "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            f"Sitemap: {base}/api/v1/seo/sitemap.xml",
            "",
        ]
    )
