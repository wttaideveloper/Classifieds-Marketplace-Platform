import logging
import os
from pathlib import Path

import requests
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

TRANSCRIBABLE_AUDIO_MIME_TYPES = {
    "audio/webm",
    "audio/m4a",
    "audio/x-m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/aac",
}


def is_transcribable_audio(mime_type: str) -> bool:
    normalized = (mime_type or "").split(";")[0].strip().lower()
    return normalized in TRANSCRIBABLE_AUDIO_MIME_TYPES


def transcribe_audio_file(file_path: str, mime_type: str) -> str:
    if not settings.speech_to_text_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Speech-to-text is not configured. Set OPENAI_API_KEY on the server.",
        )

    path = Path(file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")

    normalized_mime = (mime_type or "application/octet-stream").split(";")[0].strip().lower()
    if not is_transcribable_audio(normalized_mime):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio type for transcription: {mime_type}",
        )

    try:
        with open(path, "rb") as audio_file:
            response = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                files={"file": (path.name, audio_file, normalized_mime)},
                data={"model": settings.SPEECH_TO_TEXT_MODEL},
                timeout=120,
            )
    except requests.RequestException as exc:
        logger.exception("Speech-to-text provider request failed")
        raise HTTPException(
            status_code=502,
            detail="Speech-to-text provider request failed",
        ) from exc

    if response.status_code >= 400:
        logger.error("Speech-to-text provider error %s: %s", response.status_code, response.text)
        raise HTTPException(
            status_code=502,
            detail="Speech-to-text provider returned an error",
        )

    payload = response.json()
    transcript = (payload.get("text") or "").strip()
    if not transcript:
        raise HTTPException(status_code=502, detail="Speech-to-text returned an empty transcript")

    return transcript
