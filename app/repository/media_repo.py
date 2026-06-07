from sqlalchemy.orm import Session

from app.models.media_model import Media
from app.models.admin_model import Business
from app.models.merchant_model import MerchantListing
from uuid import UUID

def create_media_repo(
    db: Session,
    media_data: dict
):

    media = Media(**media_data)

    db.add(media)
    db.commit()
    db.refresh(media)

    return media

def get_media_by_id_repo(
    db: Session,
    media_id: UUID
):

    return (
        db.query(Media)
        .filter(Media.id == media_id)
        .first()
    )

def delete_media_repo(
    db: Session,
    media: Media
):

    db.delete(media)
    db.commit()

def get_business_by_id_repo(
    db: Session,
    business_id: UUID
):

    return (
        db.query(Business)
        .filter(Business.id == business_id)
        .first()
    )


def create_business_gallery_repo(
    db: Session,
    media_data: dict
):

    media = Media(**media_data)

    db.add(media)
    db.commit()
    db.refresh(media)

    return media

def get_listing_by_id_repo(
    db: Session,
    listing_id: UUID
):

    return (
        db.query(MerchantListing)
        .filter(MerchantListing.id == listing_id)
        .first()
    )


def create_listing_media_repo(
    db: Session,
    media_data: dict
):

    media = Media(**media_data)

    db.add(media)
    db.commit()
    db.refresh(media)

    return media