"""Reviews (verified, must be enrolled) and wishlist for Trainings."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.training_service import (
    add_training_wishlist_service,
    create_training_review_service,
    list_training_reviews_service,
    list_training_wishlist_service,
    remove_training_wishlist_service,
)


class _ReviewPayload:
    def __init__(self, rating, comment, participant_email):
        self.rating = rating
        self.comment = comment
        self.participant_email = participant_email


def test_create_review_rejects_unenrolled_participant(monkeypatch):
    from app.services import training_service

    training_id = uuid4()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: MagicMock())

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None  # no enrolment found

    with pytest.raises(HTTPException) as exc:
        create_training_review_service(db, training_id, _ReviewPayload(5, "Great!", "student@example.com"))
    assert exc.value.status_code == 400
    assert "enrolled" in exc.value.detail.lower()


def test_create_review_succeeds_for_enrolled_participant(monkeypatch):
    import datetime as _dt

    from app.services import training_service

    training_id = uuid4()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: MagicMock())

    enrolment = MagicMock()
    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "TrainingEnrolment":
            q.filter.return_value.first.return_value = enrolment
        elif model.__name__ == "TrainingReview":
            q.filter.return_value.first.return_value = None  # no existing review -> create new
        return q

    db.query.side_effect = query_side_effect
    # MagicMock db.add()/commit() don't trigger the ORM column default for
    # created_at the way a real flush would — simulate it.
    db.add.side_effect = lambda obj: setattr(obj, "created_at", _dt.datetime(2026, 1, 1))

    result = create_training_review_service(db, training_id, _ReviewPayload(4, "Good course", "student@example.com"))
    assert result["rating"] == 4
    assert result["verified"] is True
    db.add.assert_called_once()
    db.commit.assert_called()


def test_create_review_updates_existing_review_instead_of_duplicating(monkeypatch):
    """A second review from the same participant edits their existing one —
    the unique (training_id, participant_email) index means a naive insert
    would otherwise raise an IntegrityError."""
    from app.services import training_service

    training_id = uuid4()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: MagicMock())

    enrolment = MagicMock()
    existing_review = MagicMock(rating="3", comment="old comment")
    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "TrainingEnrolment":
            q.filter.return_value.first.return_value = enrolment
        elif model.__name__ == "TrainingReview":
            q.filter.return_value.first.return_value = existing_review
        return q

    db.query.side_effect = query_side_effect

    create_training_review_service(db, training_id, _ReviewPayload(5, "Actually great", "student@example.com"))

    assert existing_review.rating == "5"
    assert existing_review.comment == "Actually great"
    db.add.assert_not_called()  # updated in place, not re-inserted


def test_list_reviews_computes_average(monkeypatch):
    from app.services import training_service

    training_id = uuid4()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: MagicMock())

    rows = [
        MagicMock(id=uuid4(), training_id=training_id, rating="5", comment="a", participant_email="a@x.com", created_at=__import__("datetime").datetime(2026, 1, 1)),
        MagicMock(id=uuid4(), training_id=training_id, rating="3", comment="b", participant_email="b@x.com", created_at=__import__("datetime").datetime(2026, 1, 2)),
    ]
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows

    result = list_training_reviews_service(db, training_id)
    assert result["count"] == 2
    assert result["average_rating"] == 4.0


def test_add_and_remove_wishlist(monkeypatch):
    import datetime as _dt

    from app.services import training_service

    user_id = uuid4()
    training_id = uuid4()
    training = MagicMock(id=training_id, title="Widgets 101", primary_image=None, price="0", currency="USD")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None  # not already saved
    db.query.return_value.filter.return_value.all.return_value = []  # no reviews yet
    db.add.side_effect = lambda obj: setattr(obj, "created_at", _dt.datetime(2026, 1, 1))

    result = add_training_wishlist_service(db, user_id, training_id)
    assert result["training_id"] == str(training_id)
    assert result["title"] == "Widgets 101"
    db.add.assert_called_once()


def test_remove_wishlist_not_found_raises_404(monkeypatch):
    user_id = uuid4()
    training_id = uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc:
        remove_training_wishlist_service(db, user_id, training_id)
    assert exc.value.status_code == 404
