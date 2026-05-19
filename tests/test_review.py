from uuid import uuid4
import pytest
from app.schemas.review_schema import ReviewCreateSchema, ReviewModerationSchema, ReviewModerationStatus


def test_review_create_schema_accepts_valid_payload():
    payload = {
        "business_id": uuid4(),
        "booking_id": uuid4(),
        "rating": 5,
        "review_comment": "Excellent service",
    }

    schema = ReviewCreateSchema(**payload)

    assert schema.business_id == payload["business_id"]
    assert schema.rating == 5
    assert schema.review_comment == "Excellent service"


def test_review_create_schema_rejects_invalid_rating():
    with pytest.raises(ValueError):
        ReviewCreateSchema(
            business_id=uuid4(),
            booking_id=uuid4(),
            rating=6,
            review_comment="Too high rating",
        )


def test_review_moderation_schema_accepts_allowed_status():
    schema = ReviewModerationSchema(
        moderation_status=ReviewModerationStatus.approved,
        remarks="Looks good",
    )

    assert schema.moderation_status == ReviewModerationStatus.approved
    assert schema.remarks == "Looks good"


def test_review_moderation_schema_rejects_invalid_status():
    with pytest.raises(ValueError):
        ReviewModerationSchema(
            moderation_status="InvalidStatus",
            remarks="Bad status",
        )
