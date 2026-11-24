from datetime import date, datetime
from uuid import UUID


def conversation_room(conversation_id) -> str:
    return f"conversation:{conversation_id}"


def user_room(user_id) -> str:
    return f"user:{user_id}"


def serialize(value):
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return serialize(value.model_dump())
    return value
