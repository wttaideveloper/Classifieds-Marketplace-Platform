from datetime import datetime
from uuid import UUID

from jose import jwt

from app.core.config import settings
from app.realtime.auth import authenticate_token, extract_token_from_environ
from app.realtime.rooms import conversation_room, serialize, user_room


def test_conversation_room():
    conv_id = "550e8400-e29b-41d4-a716-446655440000"
    assert conversation_room(conv_id) == f"conversation:{conv_id}"


def test_user_room():
    user_id = "550e8400-e29b-41d4-a716-446655440001"
    assert user_room(user_id) == f"user:{user_id}"


def test_serialize_uuid_and_datetime():
    value = {
        "id": UUID("550e8400-e29b-41d4-a716-446655440000"),
        "created_at": datetime(2026, 7, 7, 12, 0, 0),
    }
    result = serialize(value)
    assert result["id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert result["created_at"] == "2026-07-07T12:00:00"


def test_authenticate_token_valid():
    token = jwt.encode(
        {"id": "550e8400-e29b-41d4-a716-446655440000", "role": "customer"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    user = authenticate_token(token)
    assert user is not None
    assert user["id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert user["role"] == "customer"


def test_authenticate_token_invalid():
    assert authenticate_token("not-a-valid-token") is None
    assert authenticate_token(None) is None


def test_extract_token_from_auth():
    token = jwt.encode({"sub": "user-123"}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    result = extract_token_from_environ({}, {"token": token})
    assert result == token


def test_extract_token_from_query_string():
    token = "abc123"
    environ = {"QUERY_STRING": f"token={token}&foo=bar"}
    result = extract_token_from_environ(environ, None)
    assert result == token
