import json
from dataclasses import dataclass
from typing import Iterable

import requests

from app.core.config import settings


@dataclass
class PushDeliveryResult:
    success: bool
    provider: str
    delivered_count: int = 0
    failed_count: int = 0
    error: str | None = None


class PushProvider:
    def send_to_tokens(
        self,
        *,
        tokens: Iterable[str],
        title: str,
        message: str,
        data: dict[str, str],
    ) -> PushDeliveryResult:
        raise NotImplementedError


class LocalPushProvider(PushProvider):
    def send_to_tokens(
        self,
        *,
        tokens: Iterable[str],
        title: str,
        message: str,
        data: dict[str, str],
    ) -> PushDeliveryResult:
        token_list = list(tokens)
        return PushDeliveryResult(
            success=bool(token_list),
            provider="local",
            delivered_count=len(token_list),
            failed_count=0 if token_list else 1,
            error=None if token_list else "No active device tokens found",
        )


class OneSignalPushProvider(PushProvider):
    def send_to_tokens(
        self,
        *,
        tokens: Iterable[str],
        title: str,
        message: str,
        data: dict[str, str],
    ) -> PushDeliveryResult:
        token_list = list(tokens)
        if not settings.ONESIGNAL_APP_ID or not settings.ONESIGNAL_REST_API_KEY:
            return PushDeliveryResult(
                success=False,
                provider="onesignal",
                failed_count=len(token_list) or 1,
                error="OneSignal credentials are not configured",
            )
        if not token_list:
            return PushDeliveryResult(
                success=False,
                provider="onesignal",
                failed_count=1,
                error="No active device tokens found",
            )

        response = requests.post(
            "https://api.onesignal.com/notifications",
            headers={
                "Authorization": f"Key {settings.ONESIGNAL_REST_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "app_id": settings.ONESIGNAL_APP_ID,
                "include_subscription_ids": token_list,
                "headings": {"en": title},
                "contents": {"en": message},
                "data": data,
            },
            timeout=20,
        )
        if response.status_code >= 400:
            return PushDeliveryResult(
                success=False,
                provider="onesignal",
                failed_count=len(token_list),
                error=response.text,
            )
        return PushDeliveryResult(
            success=True,
            provider="onesignal",
            delivered_count=len(token_list),
        )


class FcmPushProvider(PushProvider):
    def send_to_tokens(
        self,
        *,
        tokens: Iterable[str],
        title: str,
        message: str,
        data: dict[str, str],
    ) -> PushDeliveryResult:
        token_list = list(tokens)
        if not token_list:
            return PushDeliveryResult(
                success=False,
                provider="fcm",
                failed_count=1,
                error="No active device tokens found",
            )
        access_token = _get_fcm_access_token()
        if not settings.FCM_PROJECT_ID or not access_token:
            return PushDeliveryResult(
                success=False,
                provider="fcm",
                failed_count=len(token_list),
                error="FCM project ID or service account credentials are not configured",
            )

        delivered = 0
        failed = 0
        last_error = None
        endpoint = (
            f"https://fcm.googleapis.com/v1/projects/"
            f"{settings.FCM_PROJECT_ID}/messages:send"
        )
        for token in token_list:
            response = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "message": {
                        "token": token,
                        "notification": {"title": title, "body": message},
                        "data": data,
                    }
                },
                timeout=20,
            )
            if response.status_code >= 400:
                failed += 1
                last_error = response.text
            else:
                delivered += 1

        return PushDeliveryResult(
            success=delivered > 0,
            provider="fcm",
            delivered_count=delivered,
            failed_count=failed,
            error=last_error,
        )


def get_push_provider() -> PushProvider:
    provider = settings.PUSH_PROVIDER.lower()
    if provider == "onesignal":
        return OneSignalPushProvider()
    if provider == "fcm":
        return FcmPushProvider()
    return LocalPushProvider()


def _get_fcm_access_token() -> str | None:
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
    except ImportError:
        return None

    info = None
    if settings.FCM_SERVICE_ACCOUNT_JSON:
        info = json.loads(settings.FCM_SERVICE_ACCOUNT_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/firebase.messaging"]
        )
    elif settings.FCM_SERVICE_ACCOUNT_FILE:
        credentials = service_account.Credentials.from_service_account_file(
            settings.FCM_SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
    else:
        return None

    credentials.refresh(Request())
    return credentials.token
