"""One way to call a provider's REST API and turn every failure into a ProviderError the meeting service understands."""
import requests

from app.core.config import settings
from app.services.meeting_providers.base import ProviderAuthError, ProviderError


def call(session, method: str, url: str, *, headers=None, data=None, json=None, params=None, auth=None, expect_json: bool = True, auth_statuses=(401,)):
    """Returns the decoded JSON body ({} when there is none). Never returns a token in an error message."""
    try:
        response = (session or requests).request(
            method, url, headers=headers, data=data, json=json, params=params, auth=auth, timeout=settings.MEETING_PROVIDER_TIMEOUT_SECONDS)
    except requests.Timeout:
        raise ProviderError("The meeting provider did not answer in time", code="provider_timeout", retryable=True)
    except requests.RequestException as exc:
        raise ProviderError(f"Could not reach the meeting provider: {type(exc).__name__}", code="network_error", retryable=True)
    status = response.status_code
    if status in auth_statuses:
        raise ProviderAuthError()
    if status == 429 or status >= 500:
        raise ProviderError("The meeting provider is busy. Try again shortly", code="provider_unavailable", retryable=True)
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    if status >= 400:
        message = _message(body) or f"The meeting provider refused the request ({status})"
        raise ProviderError(message, code="provider_rejected")
    return body if expect_json else {}


def _message(body) -> str | None:
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        return error.get("message")
    return body.get("message") or body.get("error_description") or (error if isinstance(error, str) else None)
