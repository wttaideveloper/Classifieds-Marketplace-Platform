"""Product attributes with photos and videos (dynamic_attributes.media): stored, returned with absolute https URLs, base64 refused."""
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.database import Base
from app.models.attribute_model import DynamicAttribute
from app.models.enterprise_model import Enterprise
from app.models.product_model import Product
from app.schemas.attribute_schema import AttributeCreate, AttributeUpdate
from app.services.attribute_service import create_attribute_service, get_attributes_service, update_attribute_service

PHOTO = {"url": "https://api.example.test/api/v1/products/upload/" + "a" * 32 + ".jpg", "type": "image", "name": "front.jpg", "size": 1200, "mime": "image/jpeg"}
CLIP = {"url": "/api/v1/products/upload/" + "b" * 32 + ".mp4", "type": "video", "name": "demo.mp4"}


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://api.example.test")
    monkeypatch.setattr(settings, "PUBLIC_MEDIA_BASE_URL", "")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Enterprise.__table__, Product.__table__, DynamicAttribute.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def product(db):
    ent = Enterprise(tenant_id=uuid.uuid4(), business_short_name="Calm", business_legal_name="Calm Ltd", business_email="c@example.test")
    db.add(ent)
    db.commit()
    item = Product(enterprise_id=ent.id, product_name="Mat", product_category="Yoga", product_price=10.0)
    db.add(item)
    db.commit()
    return item


def create(db, item, **extra):
    return create_attribute_service(db, AttributeCreate(entity_type="product", entity_id=item.id, attribute_name="Colour", attribute_value="Blue",
                                                        attribute_type="text", **extra))


def test_an_attribute_keeps_its_photos_and_videos_with_absolute_urls(db):
    item = product(db)

    made = create(db, item, media=[PHOTO, CLIP])

    assert [m["type"] for m in made.model_dump()["media"]] == ["image", "video"]
    assert made.media[0].url == PHOTO["url"] and made.media[1].url == "https://api.example.test" + CLIP["url"]     # relative paths become absolute
    listed = get_attributes_service(db, entity_type="product", entity_id=item.id)
    assert [m.url for m in listed.items[0].media] == [made.media[0].url, made.media[1].url]


def test_media_is_optional_and_replaced_as_a_whole_on_update(db):
    item = product(db)
    plain = create(db, item)
    assert plain.media == []

    with_photo = update_attribute_service(db, plain.id, AttributeUpdate(media=[PHOTO]))
    assert [m.url for m in with_photo.media] == [PHOTO["url"]]
    renamed = update_attribute_service(db, plain.id, AttributeUpdate(attribute_value="Red"))
    assert [m.url for m in renamed.media] == [PHOTO["url"]]                                          # untouched when not sent
    cleared = update_attribute_service(db, plain.id, AttributeUpdate(media=[]))
    assert cleared.media == []


def test_base64_unknown_types_and_too_many_items_are_refused():
    for bad in ({"url": "data:image/png;base64,iVBORw0KGgo=", "type": "image"}, {"url": "ftp://x.test/a.jpg", "type": "image"},
                {"url": "https://x.test/a.pdf", "type": "document"}):
        with pytest.raises(ValidationError):
            AttributeUpdate(media=[bad])
    with pytest.raises(ValidationError):
        AttributeUpdate(media=[PHOTO] * 21)


# ---------------------------------------------------------------- who may write attributes

def test_only_the_owning_enterprise_admin_or_a_super_admin_can_write_attributes(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints.attribute import router
    from app.core.catalog_access import CatalogAccess, get_catalog_access
    from app.db.database import get_db

    item = product(db)
    owner_tenant = db.get(Enterprise, item.enterprise_id).tenant_id
    state = {"access": CatalogAccess("public")}
    app = FastAPI()
    app.include_router(router, prefix="/attributes")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_catalog_access] = lambda: state["access"]
    client = TestClient(app, raise_server_exceptions=False)
    body = {"entity_type": "product", "entity_id": str(item.id), "attribute_name": "Colour", "attribute_value": "Blue", "attribute_type": "text", "media": [PHOTO]}

    def as_(access):
        state["access"] = access
        return client

    assert as_(CatalogAccess("public")).post("/attributes", json=body).status_code == 401
    assert as_(CatalogAccess("customer")).post("/attributes", json=body).status_code == 403
    assert as_(CatalogAccess("provider", owner_tenant, uuid.uuid4())).post("/attributes", json=body).status_code == 403
    assert as_(CatalogAccess("admin", uuid.uuid4())).post("/attributes", json=body).status_code == 403             # another business
    made = as_(CatalogAccess("admin", owner_tenant)).post("/attributes", json=body)
    assert made.status_code == 201 and made.json()["media"][0]["url"] == PHOTO["url"]
    attr_id = made.json()["id"]

    assert as_(CatalogAccess("public")).put(f"/attributes/{attr_id}", json={"media": []}).status_code == 401
    assert as_(CatalogAccess("admin", uuid.uuid4())).put(f"/attributes/{attr_id}", json={"media": []}).status_code == 403
    assert as_(CatalogAccess("admin", uuid.uuid4())).delete(f"/attributes/{attr_id}").status_code == 403
    assert as_(CatalogAccess("super_admin")).put(f"/attributes/{attr_id}", json={"media": [CLIP]}).json()["media"][0]["type"] == "video"
    assert as_(CatalogAccess("public")).get("/attributes", params={"entity_type": "product", "entity_id": str(item.id)}).status_code == 200   # reading stays public
    assert as_(CatalogAccess("admin", owner_tenant)).delete(f"/attributes/{attr_id}").status_code == 200
