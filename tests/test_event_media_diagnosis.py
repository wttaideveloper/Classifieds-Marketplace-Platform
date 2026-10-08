"""Telling apart the four reasons GET /events/media/<uuid>.<ext> answers 404 "File not found".

Production report: the media URL answers 404 on the server while the same Event media works locally. The response
is the same for every cause, so the service can name the cause (diagnose_asset_file / scan_event_media, used by
scripts/check_event_media.py) and the endpoint logs which one it was.
"""
import logging
from datetime import datetime
from uuid import uuid4

import pytest

from app.db.database import Base
from app.models.event_media_model import EventMedia
from app.services import event_media_service as media
from tests.event_sql_support import (
    API, client_for, make_enterprise, make_event, make_session, reset_overrides,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 200


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "media_root", lambda: tmp_path)
    db = make_session()
    Base.metadata.create_all(db.bind, tables=[EventMedia.__table__])
    tenant = uuid4()
    enterprise = make_enterprise(db, tenant)
    yield type("W", (), dict(db=db, root=tmp_path, tenant=tenant, enterprise=enterprise))
    reset_overrides()
    db.close()


def add_asset(w, *, ext="png", write_file=True, attached=True, field="primary_image"):
    asset = EventMedia(
        tenant_id=w.tenant, field=field, original_name=f"photo.{ext}", ext=ext, mime_type="image/png",
        size=len(PNG), created_at=datetime(2026, 1, 1), attached_at=datetime(2026, 1, 1) if attached else None,
    )
    w.db.add(asset)
    w.db.commit()
    if write_file:
        (w.root / f"{asset.id}.{ext}").write_bytes(PNG)
    return asset


def get(w, name):
    return client_for(w.db, None).get(f"{API}/media/{name}")


# --- the four causes ---------------------------------------------------------------------------------------------

def test_a_record_and_file_that_match_are_ok(world):
    asset = add_asset(world)

    result = media.diagnose_asset_file(world.db, f"{asset.id}.png")

    assert result["verdict"] == "ok"
    assert result["files_on_disk_for_this_id"] == [f"{asset.id}.png"]
    assert result["record"]["extension"] == "png" and result["size_on_disk"] == len(PNG)


def test_no_record_in_this_database(world):
    """The upload went to another database / environment."""
    unknown = uuid4()

    result = media.diagnose_asset_file(world.db, f"{unknown}.png")

    assert result["verdict"] == "no_database_record" and "record" not in result


def test_no_record_but_the_file_is_on_disk(world):
    """The file reached this server's disk but its row is in a different database."""
    unknown = uuid4()
    (world.root / f"{unknown}.png").write_bytes(PNG)

    result = media.diagnose_asset_file(world.db, f"{unknown}.png")

    assert result["verdict"] == "no_database_record"
    assert result["files_on_disk_for_this_id"] == [f"{unknown}.png"]


def test_the_extension_in_the_url_differs_from_the_stored_one(world):
    asset = add_asset(world, ext="jpg")

    result = media.diagnose_asset_file(world.db, f"{asset.id}.png")

    assert result["verdict"] == "extension_mismatch"
    assert result["record"]["extension"] == "jpg" and result["url_extension"] == "png"


def test_the_record_exists_but_the_file_is_missing_from_storage(world):
    """The usual production cause: the storage the API reads is not the storage the file was written to."""
    asset = add_asset(world, write_file=False)

    result = media.diagnose_asset_file(world.db, f"{asset.id}.png")

    assert result["verdict"] == "file_missing_on_disk"
    assert result["expected_path"] == str(world.root / f"{asset.id}.png")
    assert result["files_on_disk_for_this_id"] == []


def test_the_file_exists_under_another_extension(world):
    asset = add_asset(world, write_file=False)
    (world.root / f"{asset.id}.jpg").write_bytes(PNG)

    result = media.diagnose_asset_file(world.db, f"{asset.id}.png")

    assert result["verdict"] == "file_missing_on_disk"
    assert result["files_on_disk_for_this_id"] == [f"{asset.id}.jpg"]


@pytest.mark.parametrize("name", ["", "photo.png", "not-a-uuid.png", "7ba4bf78-7c1c-466e-96c5-9c5929c7bb57", "../etc/passwd"])
def test_a_name_that_is_not_uuid_dot_extension(world, name):
    assert media.diagnose_asset_file(world.db, name)["verdict"] == "invalid_name"


def test_the_events_using_the_file_are_listed(world):
    asset = add_asset(world)
    event = make_event(
        world.db, world.tenant, enterprise=world.enterprise,
        primary_image=f"https://chat.example.test/api/v1/events/media/{asset.id}.png",
    )

    result = media.diagnose_asset_file(world.db, f"{asset.id}.png")

    assert [e["id"] for e in result["used_by_events"]] == [str(event.id)]
    assert result["used_by_events"][0]["status"] == "published"


def test_a_size_that_differs_from_the_record_is_flagged(world):
    asset = add_asset(world)
    (world.root / f"{asset.id}.png").write_bytes(PNG[:50])

    result = media.diagnose_asset_file(world.db, f"{asset.id}.png")

    assert result["verdict"] == "ok" and "differs from the recorded size" in result["note"]


# --- scanning everything -----------------------------------------------------------------------------------------

def test_scan_lists_rows_without_files_and_files_without_rows(world):
    good = add_asset(world)
    lost_used = add_asset(world, write_file=False, attached=True)
    lost_pending = add_asset(world, write_file=False, attached=False)
    stray = uuid4()
    (world.root / f"{stray}.png").write_bytes(PNG)

    report = media.scan_event_media(world.db)

    assert report["records"] == 3 and report["files_on_disk"] == 2
    assert report["records_with_missing_file_used_by_an_event"] == [f"{lost_used.id}.png"]
    assert report["records_with_missing_file_never_attached"] == [f"{lost_pending.id}.png"]
    assert report["files_on_disk_without_a_record"] == [f"{stray}.png"]
    assert f"{good.id}.png" not in str(report)


def test_scan_of_a_healthy_store_is_clean(world):
    add_asset(world)

    report = media.scan_event_media(world.db)

    assert report["records_with_missing_file_used_by_an_event"] == [] and report["files_on_disk_without_a_record"] == []


# --- the endpoint: same answer, but the log says why --------------------------------------------------------------

def test_the_endpoint_still_answers_the_same_404_and_logs_the_reason(world, caplog):
    missing_file = add_asset(world, write_file=False)
    wrong_ext = add_asset(world, ext="jpg")
    unknown = uuid4()
    with caplog.at_level(logging.WARNING, logger="app.api.v1.endpoints.event_media"):
        responses = [
            get(world, f"{unknown}.png"),
            get(world, f"{wrong_ext.id}.png"),
            get(world, f"{missing_file.id}.png"),
            get(world, "nonsense.png"),
        ]

    assert [r.status_code for r in responses] == [404, 404, 404, 404]
    assert all(r.json() == {"detail": "File not found"} for r in responses)  # nothing about the server leaks out
    log = caplog.text
    assert "no event_media record in this database" in log
    assert f"the record was stored as .jpg" in log
    assert "record exists but the file is missing at" in log
    assert "not a <uuid>.<ext> name" in log


def test_a_file_that_exists_is_served_as_before(world):
    asset = add_asset(world)
    make_event(
        world.db, world.tenant, enterprise=world.enterprise,
        primary_image=f"https://chat.example.test/api/v1/events/media/{asset.id}.png",
    )

    resp = get(world, f"{asset.id}.png")

    assert resp.status_code == 200 and resp.content == PNG
