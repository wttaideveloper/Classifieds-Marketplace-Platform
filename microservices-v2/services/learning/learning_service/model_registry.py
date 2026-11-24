"""Imports every model module so Base.metadata lists all of this service's tables."""
import learning_service.models.ebook_model  # noqa: F401
import learning_service.models.program_form_config_model  # noqa: F401
import learning_service.models.program_model  # noqa: F401
import learning_service.models.training_form_config_model  # noqa: F401
import learning_service.models.training_model  # noqa: F401
import learning_service.models.enterprise_model  # noqa: F401
import learning_service.models.location_model  # noqa: F401

OWNED_TABLES = ['media_assets', 'program_checkins', 'program_enrolments', 'program_form_assignments', 'program_form_audits', 'program_form_configuration_versions', 'program_form_configurations', 'program_reviews', 'program_surveys', 'programs', 'training_assessment_submissions', 'training_assignment_submissions', 'training_categories', 'training_ebook_audit', 'training_ebook_bookmarks', 'training_ebook_notes', 'training_ebook_progress', 'training_ebook_versions', 'training_ebooks', 'training_enrolments', 'training_form_assignments', 'training_form_audits', 'training_form_configuration_versions', 'training_form_configurations', 'training_lesson_attendance', 'training_live_sessions', 'training_notification_log', 'training_orders', 'training_progress', 'training_reviews', 'training_waitlist', 'training_wishlist_items', 'trainings']
# read-only copies of another service's reference tables (kept current by mskit.replication)
REPLICA_TABLES = ['enterprise_locations', 'enterprises']
# tables that exist only in v2 (outbox, reservations, ...): in this service's new database, not in the monolith
NEW_TABLES = []
