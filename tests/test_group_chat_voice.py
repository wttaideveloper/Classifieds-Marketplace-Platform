"""Voice messages in a group: the same upload, message and download calls Direct Chat uses."""
import pytest

from app.api.v1.endpoints import attachment as attachment_endpoint
from app.core.config import settings
from tests.group_chat_support import joined_group, world  # noqa: F401

API = "/api/v1/groups"
VOICE = b"\x1a\x45\xdf\xa3" + b"\x00" * 2048          # a WebM header followed by padding; the server does not decode audio


@pytest.fixture
def care(world, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    world.app.include_router(attachment_endpoint.router, prefix="/api/v1/attachments")
    p = world.people
    return joined_group(world, kind="care", members=[p.mehta, p.priya])


def upload(world, user, group, mime="audio/webm", name="voice.webm", data=VOICE, attachment_type="audio"):
    form = {"conversation_id": group["id"], **({"attachment_type": attachment_type} if attachment_type else {})}
    return world.as_user(user).post("/api/v1/attachments/upload", files={"file": (name, data, mime)}, data=form)


def test_a_browser_recorded_voice_note_can_be_sent_and_played_back(world, care):
    p = world.people
    up = upload(world, p.priya, care, mime="audio/webm;codecs=opus")
    assert up.status_code == 201, up.text
    assert up.json()["attachment_type"] == "audio"

    sent = world.as_user(p.priya).post(f"{API}/{care['id']}/messages", json={"message_type": "audio", "content": "", "attachment_id": up.json()["id"]})
    assert sent.status_code == 201, sent.text
    assert sent.json()["message_type"] == "audio" and sent.json()["attachment_id"] == up.json()["id"] and not sent.json()["content"]

    got = world.as_user(p.mehta).get(f"/api/v1/attachments/{up.json()['id']}?download=true")
    assert got.status_code == 200 and got.content == VOICE
    assert got.headers["content-type"] == "audio/webm;codecs=opus" and got.headers["accept-ranges"] == "bytes"
    part = world.as_user(p.mehta).get(f"/api/v1/attachments/{up.json()['id']}?download=true", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and len(part.content) == 10


def test_content_can_be_left_out_of_a_voice_message(world, care):
    p = world.people
    up = upload(world, p.priya, care).json()

    sent = world.as_user(p.priya).post(f"{API}/{care['id']}/messages", json={"message_type": "audio", "attachment_id": up["id"]})

    assert sent.status_code == 201


def test_without_attachment_type_a_webm_opus_recording_is_refused(world, care):
    assert upload(world, world.people.priya, care, mime="audio/webm;codecs=opus", attachment_type=None).status_code == 400
    assert upload(world, world.people.priya, care, mime="audio/webm", attachment_type=None).status_code == 201


def test_a_voice_note_over_the_audio_limit_is_refused(world, care, monkeypatch):
    monkeypatch.setattr("app.services.chat_attachment_service.MAX_SIZE_BYTES", {"audio": 1024, "image": 1, "document": 1, "video": 1})

    resp = upload(world, world.people.priya, care)

    assert resp.status_code == 400 and "maximum size" in resp.json()["detail"]


def test_someone_outside_the_group_cannot_upload_or_download(world, care):
    p = world.people
    up = upload(world, p.priya, care).json()

    assert upload(world, p.stranger, care).status_code == 403
    assert world.as_user(p.stranger).get(f"/api/v1/attachments/{up['id']}?download=true").status_code == 403
