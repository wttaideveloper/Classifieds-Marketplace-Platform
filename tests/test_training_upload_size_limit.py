"""Training video upload size cap: 300 MB (314,572,800 bytes = 300 MiB),
clear 413 error on rejection, and purpose auto-inference for videos."""
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services.training_upload_service import MAX_SIZE_BYTES, _infer_purpose, save_training_upload


def test_video_cap_is_300_mb_not_the_old_100_mb():
    assert settings.MAX_VIDEO_SIZE_MB == 300
    assert MAX_SIZE_BYTES["lesson_video"] == 300 * 1024 * 1024 == 314_572_800


def test_upload_just_under_the_cap_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.training_upload_service.training_upload_root", lambda: tmp_path)
    cap = MAX_SIZE_BYTES["lesson_video"]
    # Use a small stand-in cap so the test doesn't allocate 300 MB of real bytes.
    monkeypatch.setitem(MAX_SIZE_BYTES, "lesson_video", 1000)
    result = save_training_upload(b"x" * 999, "clip.mp4", "video/mp4", "lesson_video")
    assert result["size"] == 999
    assert result["purpose"] == "lesson_video"
    monkeypatch.setitem(MAX_SIZE_BYTES, "lesson_video", cap)


def test_upload_over_the_cap_is_rejected_with_a_clear_message(monkeypatch):
    monkeypatch.setitem(MAX_SIZE_BYTES, "lesson_video", 1000)
    with pytest.raises(HTTPException) as exc:
        save_training_upload(b"x" * 1001, "clip.mp4", "video/mp4", "lesson_video")
    assert exc.value.status_code == 413
    assert "lesson_video" in exc.value.detail
    assert "MB uploaded" in exc.value.detail
    assert "max 0 MB allowed" in exc.value.detail or "max" in exc.value.detail


def test_error_message_reports_actual_and_max_size_in_mb(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.training_upload_service.training_upload_root", lambda: tmp_path)
    cap = MAX_SIZE_BYTES["lesson_video"]
    oversized = cap + 10 * 1024 * 1024  # 10 MB over the real 300 MB cap
    with pytest.raises(HTTPException) as exc:
        save_training_upload(b"\x00" * oversized, "big-lecture.mp4", "video/mp4", "lesson_video")
    assert exc.value.status_code == 413
    assert "310.0 MB uploaded" in exc.value.detail
    assert "max 300 MB allowed" in exc.value.detail


@pytest.mark.parametrize("filename,content_type", [
    ("lecture.mp4", "video/mp4"),
    ("lecture.webm", "video/webm"),
    ("lecture.mov", None),
    ("recording.ogv", "audio/ogg"),  # browser-recorded dual-container clip
])
def test_video_purpose_auto_inferred(filename, content_type):
    assert _infer_purpose(content_type, filename) == "lesson_video"


def test_non_video_purpose_not_misclassified_into_the_10mb_image_cap():
    """Regression guard for the reported symptom: a video must never fall
    through to the 10 MB image cap."""
    assert _infer_purpose("video/mp4", "clip.mp4") != "image"
    assert MAX_SIZE_BYTES["lesson_video"] > MAX_SIZE_BYTES["image"]
