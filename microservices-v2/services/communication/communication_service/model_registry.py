"""Imports every model module so Base.metadata lists all of this service's tables."""
import communication_service.models.chat_model  # noqa: F401
import communication_service.models.group_chat_model  # noqa: F401
import communication_service.models.enterprise_model  # noqa: F401
import communication_service.models.location_model  # noqa: F401
import communication_service.models.event_model  # noqa: F401
import communication_service.models.event_aux_models  # noqa: F401
import communication_service.models.training_model  # noqa: F401
import communication_service.models.program_model  # noqa: F401

OWNED_TABLES = ['chat_attachments', 'chat_notifications', 'chat_subscriptions', 'conversation_participants', 'conversations', 'device_tokens', 'group_audit_log', 'group_clinical_record_entries', 'group_consents', 'group_data_requests', 'group_message_reactions', 'group_reports', 'message_read_receipts', 'messages', 'notification_preferences', 'provider_assignments', 'typing_indicators', 'user_display_names', 'user_presence', 'verified_providers']
# read-only copies of another service's reference tables (kept current by mskit.replication)
REPLICA_TABLES = ['enterprise_locations', 'enterprises', 'event_orders', 'event_registrations', 'events', 'programs', 'training_enrolments', 'trainings']
# tables that exist only in v2 (outbox, reservations, ...): in this service's new database, not in the monolith
NEW_TABLES = []
