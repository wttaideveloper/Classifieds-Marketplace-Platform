"""Base64 images on Products and Services become hosted files; responses carry `images` as a list of https URLs (app/services/inline_media.py)."""
import base64
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.database import Base
from app.models.enterprise_model import Enterprise
from app.models.product_model import Product
from app.models.service_model import Service
from app.repository import product_repo, service_repo
from app.services import inline_media
from app.services.response_mappers import map_product_list_item, map_service_list_item

JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x02" * 60


def data_uri(raw, mime="image/jpeg"):
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://api.example.test")
    monkeypatch.setattr(settings, "PUBLIC_MEDIA_BASE_URL", "")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Enterprise.__table__, Product.__table__, Service.__table__])
    db = sessionmaker(bind=engine)()
    ent = Enterprise(tenant_id=uuid.uuid4(), business_short_name="Calm Clinic", business_legal_name="Calm Clinic Ltd", business_email="calm@example.test")
    db.add(ent)
    db.commit()
    yield SimpleNamespace(db=db, ent=ent, root=tmp_path / "products")
    db.close()
    engine.dispose()


def payload(**values):
    return SimpleNamespace(model_dump=lambda **_: dict(values))


def new_product(env, images):
    return product_repo.create_product(env.db, payload(enterprise_id=env.ent.id, product_name="Mat", product_category="Yoga", product_price=10.0, product_images=images))


def stored_files(env):
    return sorted(p.name for p in env.root.iterdir()) if env.root.exists() else []


def test_data_uris_with_their_commas_are_split_correctly_and_mixed_with_urls():
    value = f"{data_uri(JPEG)}, https://cdn.example.test/a.jpg,{data_uri(PNG, 'image/png')} , /api/v1/products/upload/{'a' * 32}.jpg"
    found = inline_media.entries(value)
    assert [f[:15] for f in found] == ["data:image/jpeg", "https://cdn.exa", "data:image/png;", "/api/v1/product"]
    assert inline_media.entries('["https://x.test/1.jpg", "https://x.test/2.jpg"]') == ["https://x.test/1.jpg", "https://x.test/2.jpg"]
    assert inline_media.entries(None) == [] and inline_media.entries("  ") == []


def test_a_product_saved_with_base64_keeps_hosted_urls_instead(env):
    product = new_product(env, f"{data_uri(JPEG)}, https://cdn.example.test/kept.jpg, {data_uri(PNG, 'image/png')}")

    assert "base64" not in product.product_images
    urls = [u.strip() for u in product.product_images.split(",")]
    assert len(urls) == 3 and urls[1] == "https://cdn.example.test/kept.jpg"
    assert all(u.startswith("https://api.example.test/api/v1/products/upload/") for u in (urls[0], urls[2]))
    files = stored_files(env)
    assert len(files) == 2 and {f.rsplit(".", 1)[1] for f in files} == {"jpg", "png"}
    assert (env.root / urls[0].rsplit("/", 1)[1]).read_bytes() == JPEG

    mapped = map_product_list_item(product)
    assert mapped["images"] == urls and mapped["image_urls"] == urls and mapped["product_images"] == product.product_images      # array for clients, legacy string kept


def test_an_update_converts_too_and_plain_urls_are_untouched(env):
    product = new_product(env, "https://cdn.example.test/one.jpg")
    assert product.product_images == "https://cdn.example.test/one.jpg" and stored_files(env) == []

    product = product_repo.update_product(env.db, product, payload(product_images=data_uri(JPEG)))
    assert product.product_images.startswith("https://api.example.test/api/v1/products/upload/") and len(stored_files(env)) == 1


def test_a_service_banner_is_converted_and_listed_as_images(env):
    service = service_repo.create_service(env.db, payload(enterprise_id=env.ent.id, service_name="Yoga", service_category="Fitness", service_price=0.0, duration=60,
                                                          banner_image=data_uri(PNG, "image/png")))
    assert service.banner_image.startswith("https://api.example.test/api/v1/products/upload/") and service.banner_image.endswith(".png")
    assert map_service_list_item(service)["images"] == [service.banner_image]


def test_bad_inline_images_are_refused_and_nothing_is_saved(env, monkeypatch):
    cases = [
        (data_uri(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"), 415),
        (data_uri(b"not really a jpeg at all", "image/jpeg"), 415),
        ("data:image/jpeg;base64,@@@not-base64@@@", 422),
    ]
    for value, code in cases:
        with pytest.raises(HTTPException) as caught:
            new_product(env, value)
        assert caught.value.status_code == code, value[:30]
    monkeypatch.setattr(settings, "PRODUCT_MAX_IMAGE_MB", 0)
    with pytest.raises(HTTPException) as caught:
        new_product(env, data_uri(JPEG))
    assert caught.value.status_code == 413
    env.db.rollback()
    assert env.db.query(Product).count() == 0 and stored_files(env) == []


def test_images_never_contain_base64_even_for_a_row_not_converted_yet(env):
    row = Product(enterprise_id=env.ent.id, product_name="Old", product_category="Yoga", product_price=1.0,
                  product_images=f"{data_uri(JPEG)}, https://cdn.example.test/ok.jpg")
    env.db.add(row)
    env.db.commit()
    assert map_product_list_item(row)["images"] == ["https://cdn.example.test/ok.jpg"]


def test_image_urls_can_be_sent_as_a_list_and_always_come_back_as_https_urls(env):
    from app.schemas.product_schema import ProductCreate, ProductUpdate

    created = ProductCreate.model_validate({"enterprise_id": str(env.ent.id), "product_name": "Mat", "category": "Yoga", "price": 10.0,
                                            "image_urls": ["https://cdn.example.test/a.jpg", "https://cdn.example.test/b.jpg"]})
    assert created.to_model_data()["product_images"] == "https://cdn.example.test/a.jpg, https://cdn.example.test/b.jpg"
    assert ProductUpdate.model_validate({"image_urls": ["https://cdn.example.test/c.jpg"]}).to_model_data()["product_images"] == "https://cdn.example.test/c.jpg"
    product = new_product(env, "https://cdn.example.test/a.jpg, https://cdn.example.test/b.jpg")
    assert map_product_list_item(product)["image_urls"] == ["https://cdn.example.test/a.jpg", "https://cdn.example.test/b.jpg"]
    service = service_repo.create_service(env.db, payload(enterprise_id=env.ent.id, service_name="Yoga", service_category="Fitness", service_price=0.0, duration=60))
    assert map_service_list_item(service)["image_urls"] == []
