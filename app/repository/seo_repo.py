from sqlalchemy.orm import Session

from app.models.seo_model import SeoMetadata, SitemapEntry


def get_seo_by_id(db: Session, seo_id):
    return db.query(SeoMetadata).filter(SeoMetadata.id == seo_id).first()


def get_seo_by_entity(db: Session, entity_type: str, entity_id):
    return (
        db.query(SeoMetadata)
        .filter(
            SeoMetadata.entity_type == entity_type,
            SeoMetadata.entity_id == entity_id,
        )
        .first()
    )


def get_seo_by_slug(db: Session, slug: str):
    return db.query(SeoMetadata).filter(SeoMetadata.slug == slug).first()


def save_seo(db: Session, seo: SeoMetadata):
    try:
        db.add(seo)
        db.commit()
        db.refresh(seo)
        return seo
    except Exception:
        db.rollback()
        raise


def update_seo(db: Session, seo: SeoMetadata):
    try:
        db.commit()
        db.refresh(seo)
        return seo
    except Exception:
        db.rollback()
        raise


def get_sitemap_entry_by_entity(db: Session, entity_type: str, entity_id):
    return (
        db.query(SitemapEntry)
        .filter(
            SitemapEntry.entity_type == entity_type,
            SitemapEntry.entity_id == entity_id,
        )
        .first()
    )


def upsert_sitemap_entry(db: Session, entry: SitemapEntry):
    try:
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry
    except Exception:
        db.rollback()
        raise


def list_sitemap_entries(db: Session):
    return db.query(SitemapEntry).order_by(SitemapEntry.last_modified.desc()).all()
