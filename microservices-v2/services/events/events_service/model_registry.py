"""Imports every model module so Base.metadata lists all of this service's tables."""
import events_service.models.event_aux_models  # noqa: F401
import events_service.models.event_discussion_model  # noqa: F401
import events_service.models.event_form_config_model  # noqa: F401
import events_service.models.event_media_model  # noqa: F401
import events_service.models.event_model  # noqa: F401
import events_service.models.event_type_model  # noqa: F401
import events_service.models.enterprise_model  # noqa: F401
import events_service.models.location_model  # noqa: F401

OWNED_TABLES = ['event_audits', 'event_categories', 'event_discussion_members', 'event_discussion_messages', 'event_discussion_moderation', 'event_discussion_nudges', 'event_feedback', 'event_form_assignments', 'event_form_audits', 'event_form_configuration_versions', 'event_form_configurations', 'event_media', 'event_orders', 'event_registration_options', 'event_registrations', 'event_session_attendance', 'event_templates', 'event_types', 'event_waitlist', 'event_wishlist_items', 'events']
# read-only copies of another service's reference tables (kept current by mskit.replication)
REPLICA_TABLES = ['enterprise_locations', 'enterprises']
# tables that exist only in v2 (outbox, reservations, ...): in this service's new database, not in the monolith
import events_service.event_order_api  # noqa: F401,E402
NEW_TABLES = ['outbox_messages', 'processed_messages']
