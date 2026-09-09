"""Saved addresses (address book) — GET/POST /api/v1/addresses/."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.address_model import UserAddress
from app.schemas.address_schema import AddressCreate
from app.services.address_service import create_address_service, get_addresses_service

_USER = {"id": "a5f3c1e0-1111-4a2b-9c3d-000000000001"}


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    UserAddress.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _payload(**overrides):
    base = dict(
        label="Home",
        full_name="Suresh Inti",
        phone="+1 415 555 0198",
        line1="2214 SE Ash St",
        line2="Apt 2",
        city="Portland",
        state="OR",
        zip="97214",
        country="United States",
        is_default=False,
    )
    base.update(overrides)
    return AddressCreate(**base)


def test_create_address_returns_the_saved_object(db):
    result = create_address_service(db, _USER, _payload())
    assert result.full_name == "Suresh Inti"
    assert result.line1 == "2214 SE Ash St"
    assert result.is_default is False
    assert result.id is not None


def test_list_addresses_returns_created_items(db):
    create_address_service(db, _USER, _payload(label="Home"))
    create_address_service(db, _USER, _payload(label="Work", line1="100 Market St", zip="97201"))

    listing = get_addresses_service(db, _USER)
    labels = {item.label for item in listing.items}
    assert labels == {"Home", "Work"}


def test_marking_a_new_address_default_unsets_the_previous_default(db):
    create_address_service(db, _USER, _payload(label="Home", is_default=True))
    create_address_service(db, _USER, _payload(label="Work", line1="100 Market St", zip="97201", is_default=True))

    listing = get_addresses_service(db, _USER)
    defaults = [item for item in listing.items if item.is_default]
    assert len(defaults) == 1
    assert defaults[0].label == "Work"


def test_addresses_are_scoped_per_user(db):
    create_address_service(db, _USER, _payload(label="Home"))
    other_user = {"id": "b6f4d2f1-2222-4b3c-8d4e-000000000002"}
    create_address_service(db, other_user, _payload(label="Elsewhere"))

    listing = get_addresses_service(db, _USER)
    assert [item.label for item in listing.items] == ["Home"]
