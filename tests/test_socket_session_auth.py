from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import settings
from app.main import socket_app
from app.realtime import events
from app.realtime.auth import extract_token_from_environ
from app.realtime.server import SOCKETIO_PATH, sio

USER_ID = "550e8400-e29b-41d4-a716-446655440000"


def token(expired=False):
    return jwt.encode({"id":USER_ID,"sub":USER_ID,"role":"provider",
                       "exp":datetime.now(timezone.utc)+timedelta(minutes=-1 if expired else 5)},
                      settings.SECRET_KEY,algorithm=settings.ALGORITHM)


@pytest.fixture
def handshake(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_DEV_TOKEN", False)
    monkeypatch.setattr(sio.eio, "cors_allowed_origins", ["http://testserver"])
    monkeypatch.setattr(events, "SessionLocal", MagicMock())
    presence=MagicMock()
    monkeypatch.setattr(events, "update_presence_service", presence)
    monkeypatch.setattr(events, "emit_user_online", AsyncMock())
    monkeypatch.setattr(events, "emit_user_offline", AsyncMock())
    return TestClient(socket_app), presence


@pytest.mark.parametrize("source", ["cookie", "fallback_cookie", "bearer"])
def test_cookie_or_bff_bearer_handshake_without_js_token(handshake, monkeypatch, source):
    client,presence=handshake
    headers={"origin":"http://testserver", "upgrade":"websocket"}
    credential=token()
    if source=='bearer':
        headers['authorization']='Bearer '+credential
    else:
        monkeypatch.setattr(settings, 'WEB_SESSION_COOKIE_FALLBACK_NAMES','legacy_session')
        name=settings.WEB_SESSION_COOKIE_NAME if source=='cookie' else 'legacy_session'
        headers['cookie']=f'{name}={credential}'
    with client.websocket_connect(f'{SOCKETIO_PATH}/?EIO=4&transport=websocket',headers=headers) as ws:
        assert ws.receive_text().startswith('0')  # Engine.IO transport open
        ws.send_text('40')  # Socket.IO namespace connect; no auth payload
        assert ws.receive_text().startswith('40')
        assert presence.call_args.args[1]['id']==USER_ID


@pytest.mark.parametrize("credential", [None,"invalid", "expired"])
def test_missing_invalid_expired_cookie_is_rejected(handshake, credential):
    client,presence=handshake
    headers={'origin':'http://testserver', 'upgrade':'websocket'}
    if credential:
        headers['cookie']=f'{settings.WEB_SESSION_COOKIE_NAME}={token(True) if credential=="expired" else credential}'
    with client.websocket_connect(f'{SOCKETIO_PATH}/?EIO=4&transport=websocket',headers=headers) as ws:
        ws.receive_text(); ws.send_text('40')
        assert ws.receive_text().startswith('44')
        presence.assert_not_called()


def test_untrusted_origin_rejected_before_session(handshake):
    client,presence=handshake
    response=client.get(f'{SOCKETIO_PATH}/?EIO=4&transport=polling',headers={
        'origin':'https://untrusted.example','cookie':f'{settings.WEB_SESSION_COOKIE_NAME}={token()}'})
    assert response.status_code==400
    presence.assert_not_called()


def test_credentialed_polling_cors(handshake):
    client,_=handshake
    response=client.get(f'{SOCKETIO_PATH}/?EIO=4&transport=polling',headers={'origin':'http://testserver', 'upgrade':'websocket'})
    assert response.status_code==200
    assert response.headers['access-control-allow-origin']=='http://testserver'
    assert response.headers['access-control-allow-credentials']=='true'


def test_asgi_cookie_and_bff_bearer_headers():
    assert extract_token_from_environ({'asgi.scope':{'headers':[(b'cookie',f'{settings.WEB_SESSION_COOKIE_NAME}=cookie'.encode())]}},None)=='cookie'
    assert extract_token_from_environ({'asgi.scope':{'headers':[(b'authorization',b'Bearer forwarded')]}},None)=='forwarded'
    assert extract_token_from_environ({'HTTP_AUTHORIZATION':'Bearer forwarded','HTTP_COOKIE':f'{settings.WEB_SESSION_COOKIE_NAME}=cookie'},None)=='forwarded'


def test_invalid_session_does_not_fall_back_to_enabled_dev_user(handshake, monkeypatch):
    client,presence=handshake
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'development')
    monkeypatch.setattr(settings, 'ENABLE_DEV_TOKEN', True)
    with client.websocket_connect(f'{SOCKETIO_PATH}/?EIO=4&transport=websocket',headers={
        'origin':'http://testserver','upgrade':'websocket','cookie':f'{settings.WEB_SESSION_COOKIE_NAME}=invalid'}) as ws:
        ws.receive_text(); ws.send_text('40')
        assert ws.receive_text().startswith('44')
        presence.assert_not_called()


def test_unconfigured_origins_use_same_origin_check(handshake, monkeypatch):
    client,_=handshake
    monkeypatch.setattr(sio.eio, 'cors_allowed_origins', None)
    response=client.get(f'{SOCKETIO_PATH}/?EIO=4&transport=polling',headers={'origin':'https://untrusted.example'})
    assert response.status_code==400
