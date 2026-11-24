"""Imports every model module so Base.metadata lists all of this service's tables."""
import content_service.models.blog_model  # noqa: F401

OWNED_TABLES = ['cms_blog_categories', 'cms_blog_files', 'cms_blog_form_assignments', 'cms_blog_form_audit', 'cms_blog_form_versions', 'cms_blog_forms', 'cms_blog_governance', 'cms_blog_governance_audit', 'cms_blog_post_history', 'cms_blog_posts']
# read-only copies of another service's reference tables (kept current by mskit.replication)
REPLICA_TABLES = []
# tables that exist only in v2 (outbox, reservations, ...): in this service's new database, not in the monolith
NEW_TABLES = []
