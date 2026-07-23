from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.attachment import router as attachment_router
from app.api.v1.endpoints.message import router as message_router
from app.core.dependencies import get_current_user
from app.models.chat_model import ChatAttachment

_CONV_ID = UUID("550e8400-e29b-41d4-a716-446655440001")
_MSG_ID = UUID("550e8400-e29b-41d4-a716-446655440010")
_ATTACH_ID = UUID("550e8400-e29b-41d4-a716-446655440011")
_USER_ID = UUID("550e8400-e29b-41d4-a716-446655440020")
_NOW = datetime.utcnow()

app = FastAPI()
app.include_router(attachment_router, prefix="/attachments")
app.include_router(message_router, prefix="/messages")
app.dependency_overrides[get_current_user] = lambda: {
    "id": str(_USER_ID),
    "role": "provider",
    "email": "provider@example.com",
}

client = TestClient(app)


def _audio_attachment(*, transcript: str | None = None) -> ChatAttachment:
    return ChatAttachment(
        id=_ATTACH_ID,
        conversation_id=_CONV_ID,
        message_id=_MSG_ID,
        uploaded_by=_USER_ID,
        file_name="voice.webm",
        file_path="uploads/test/voice.webm",
        mime_type="audio/webm",
        file_size=1024,
        attachment_type="audio",
        transcript=transcript,
        transcribed_at=_NOW if transcript else None,
        created_at=_NOW,
    )


@patch("app.api.v1.endpoints.attachment.transcribe_attachment_service")
def test_transcribe_attachment_endpoint(mock_service):
    mock_service.return_value = {
        "transcript": "hello world",
        "conversation_id": str(_CONV_ID),
        "message_id": str(_MSG_ID),
        "attachment_id": str(_ATTACH_ID),
        "mime_type": "audio/webm",
        "already_transcribed": False,
        "transcribed_at": _NOW.isoformat(),
    }

    response = client.post(
        f"/attachments/{_ATTACH_ID}/transcribe",
        json={"conversation_id": str(_CONV_ID), "message_id": str(_MSG_ID)},
    )

    assert response.status_code == 200
    assert response.json()["transcript"] == "hello world"
    mock_service.assert_called_once()


@patch("app.api.v1.endpoints.message.transcribe_message_service")
def test_transcribe_message_endpoint(mock_service):
    mock_service.return_value = {
        "transcript": "cached transcript",
        "conversation_id": str(_CONV_ID),
        "message_id": str(_MSG_ID),
        "attachment_id": str(_ATTACH_ID),
        "mime_type": "audio/m4a",
        "already_transcribed": True,
        "transcribed_at": _NOW.isoformat(),
    }

    response = client.post(
        f"/messages/{_MSG_ID}/transcribe",
        json={
            "conversation_id": str(_CONV_ID),
            "attachment_id": str(_ATTACH_ID),
        },
    )

    assert response.status_code == 200
    assert response.json()["already_transcribed"] is True


@patch("app.services.transcription_service.os.path.exists", return_value=True)
@patch("app.services.transcription_service.transcribe_audio_file")
@patch("app.services.transcription_service.chat_repo.save_attachment_transcript")
@patch("app.services.transcription_service.chat_repo.get_attachment_by_id")
@patch("app.services.transcription_service.chat_repo.is_participant")
def test_transcribe_attachment_service_returns_saved_transcript(
    mock_is_participant,
    mock_get_attachment,
    mock_save,
    mock_transcribe,
    _mock_exists,
):
    from app.services.transcription_service import transcribe_attachment_service

    attachment = _audio_attachment()
    mock_is_participant.return_value = True
    mock_get_attachment.return_value = attachment
    mock_transcribe.return_value = "hello from whisper"
    mock_save.side_effect = lambda _db, att, text: _audio_attachment(transcript=text)

    result = transcribe_attachment_service(
        MagicMock(),
        {"id": str(_USER_ID), "role": "provider"},
        _ATTACH_ID,
        conversation_id=_CONV_ID,
    )

    assert result.transcript == "hello from whisper"
    assert result.already_transcribed is False
    mock_transcribe.assert_called_once()


@patch("app.services.transcription_service.chat_repo.get_attachment_by_id")
@patch("app.services.transcription_service.chat_repo.is_participant")
def test_transcribe_attachment_service_uses_cached_transcript(
    mock_is_participant,
    mock_get_attachment,
):
    from app.services.transcription_service import transcribe_attachment_service

    mock_is_participant.return_value = True
    mock_get_attachment.return_value = _audio_attachment(transcript="already done")

    result = transcribe_attachment_service(
        MagicMock(),
        {"id": str(_USER_ID), "role": "provider"},
        _ATTACH_ID,
    )

    assert result.transcript == "already done"
    assert result.already_transcribed is True
