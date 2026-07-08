"""Seed sample chat conversations for web/admin testing.

Usage:
    python scripts/seed_chat.py

Creates an open conversation between the static test customer and provider users,
with sample messages assigned to the provider.
"""

from datetime import datetime
from uuid import UUID

from app.db.database import SessionLocal
from app.models.chat_model import (
    Conversation,
    ConversationParticipant,
    Message,
    ProviderAssignment,
)
from app.schemas.auth_schema import (
    TEST_CUSTOMER_USER_ID,
    TEST_PROVIDER_USER_ID,
)

CUSTOMER_ID = UUID(TEST_CUSTOMER_USER_ID)
PROVIDER_ID = UUID(TEST_PROVIDER_USER_ID)


def seed_chat():
    db = SessionLocal()
    try:
        existing = (
            db.query(Conversation)
            .join(ConversationParticipant)
            .filter(ConversationParticipant.user_id == PROVIDER_ID)
            .filter(Conversation.status == "open")
            .first()
        )
        if existing:
            print(f"Chat seed already exists. Conversation ID: {existing.id}")
            return existing.id

        conversation = Conversation(
            subject="Welcome to Pinnacle Wellness",
            conversation_type="standard",
            status="open",
            created_by=CUSTOMER_ID,
            assigned_provider_id=PROVIDER_ID,
            last_message_at=datetime.utcnow(),
            last_message_preview="Happy to help with your onboarding questions.",
        )
        conversation.participants = [
            ConversationParticipant(user_id=CUSTOMER_ID, role="customer"),
            ConversationParticipant(user_id=PROVIDER_ID, role="provider"),
        ]
        db.add(conversation)
        db.flush()

        messages = [
            Message(
                conversation_id=conversation.id,
                sender_id=CUSTOMER_ID,
                content="Hi, I need help setting up my enterprise profile.",
                message_type="text",
            ),
            Message(
                conversation_id=conversation.id,
                sender_id=PROVIDER_ID,
                content="Happy to help with your onboarding questions.",
                message_type="text",
            ),
            Message(
                conversation_id=conversation.id,
                sender_id=CUSTOMER_ID,
                content="Can you review my business details before I publish?",
                message_type="text",
            ),
        ]
        for message in messages:
            db.add(message)

        db.add(
            ProviderAssignment(
                conversation_id=conversation.id,
                provider_id=PROVIDER_ID,
                assigned_by=CUSTOMER_ID,
            )
        )

        db.commit()
        db.refresh(conversation)
        print("Chat seed created successfully.")
        print(f"  Conversation ID: {conversation.id}")
        print(f"  Provider user ID: {PROVIDER_ID}")
        print(f"  Customer user ID: {CUSTOMER_ID}")
        print("  Use GET /api/v1/auth/dev-token with provider role to test /admin/messages.")
        return conversation.id
    finally:
        db.close()


if __name__ == "__main__":
    seed_chat()
