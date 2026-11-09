"""AI moderation of a discussion message: safe, flagged, or extreme.

Two layers, so the policy does not depend on one model's languages:
  1. word lists the client maintains (EVENT_DISCUSSION_BLOCK_WORDS / _FLAG_WORDS), for Tamil, Tanglish and local slang. Whole words, any case.
  2. the AI (OpenAI's moderation endpoint, `EVENT_DISCUSSION_MODERATION=openai`).
If the AI cannot answer, `ModerationUnavailable` is raised and the message stays unpublished until it can be judged (never published by default).
`EVENT_DISCUSSION_MODERATION=off` is the same as the AI being down. `rules` uses only the word lists.
Tests replace `classify` with a stub.
"""
import logging
import re
from dataclasses import dataclass, field

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

# Categories that mean "extreme" when scored high: threats, hate with threats, sexual content with minors, graphic violence, self-harm instructions
EXTREME = ("harassment/threatening", "hate/threatening", "sexual/minors", "violence/graphic", "self-harm/instructions", "illicit/violent")


class ModerationUnavailable(Exception):
    pass


@dataclass
class Verdict:
    label: str                                   # safe | flagged | extreme
    reason: str | None = None
    categories: list[str] = field(default_factory=list)
    provider: str = "rules"


def _words(raw: str) -> list[str]:
    return [w.strip().lower() for w in (raw or "").split(",") if w.strip()]


def _hit(text: str, words: list[str]) -> str | None:
    lowered = text.lower()
    for word in words:
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered):
            return word
    return None


def _label(category: str) -> str:
    return category.replace("/", " - ").replace("_", " ")


def _openai(text: str) -> Verdict:
    if not settings.OPENAI_API_KEY.strip():
        raise ModerationUnavailable("The moderation service is not configured")
    try:
        response = requests.post(
            "https://api.openai.com/v1/moderations", headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            json={"model": "omni-moderation-latest", "input": text}, timeout=settings.EVENT_DISCUSSION_MODERATION_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        result = response.json()["results"][0]
    except Exception as exc:
        raise ModerationUnavailable(f"{type(exc).__name__}") from exc
    scores = result.get("category_scores") or {}
    flags = result.get("categories") or {}
    extreme = [c for c in EXTREME if scores.get(c, 0) >= settings.EVENT_DISCUSSION_BLOCK_THRESHOLD or (flags.get(c) and scores.get(c, 0) >= 0.5)]
    if extreme:
        return Verdict("extreme", "AI detected severe content: " + ", ".join(_label(c) for c in extreme), extreme, "openai")
    flagged = sorted({c for c, on in flags.items() if on} | {c for c, s in scores.items() if s >= settings.EVENT_DISCUSSION_FLAG_THRESHOLD})
    if flagged:
        return Verdict("flagged", "AI flagged: " + ", ".join(_label(c) for c in flagged), flagged, "openai")
    return Verdict("safe", None, [], "openai")


def classify(text: str) -> Verdict:
    """Judge one message. Raises ModerationUnavailable when the AI cannot be reached."""
    word = _hit(text, _words(settings.EVENT_DISCUSSION_BLOCK_WORDS))
    if word:
        return Verdict("extreme", "Contains a word on the blocked list", ["blocklist"], "wordlist")
    provider = (settings.EVENT_DISCUSSION_MODERATION or "off").lower()
    if provider == "openai":
        verdict = _openai(text)
    elif provider == "rules":
        verdict = Verdict("safe", None, [], "rules")
    else:
        raise ModerationUnavailable("AI moderation is switched off")
    if verdict.label == "safe":
        word = _hit(text, _words(settings.EVENT_DISCUSSION_FLAG_WORDS))
        if word:
            return Verdict("flagged", "Contains a word on the review list", ["wordlist"], verdict.provider)
    return verdict
