"""Which service owns each monolith module and table (None = domain-free shared utility)."""
from __future__ import annotations

import ast
import json
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
APP = V2.parent / "app"

MODEL_DOMAIN = {
    "address_model": "identity", "attribute_model": "catalog", "blog_model": "content", "cart_model": "commerce",
    "chat_model": "communication", "ebook_model": "learning", "enterprise_model": "catalog", "event_aux_models": "events",
    "event_discussion_model": "events", "event_form_config_model": "events", "event_media_model": "events",
    "event_model": "events", "event_type_model": "events", "group_chat_model": "communication", "location_model": "catalog",
    "meeting_model": "meetings", "notification_model": "notifications", "onboarding_form_model": "catalog",
    "payment_model": "commerce", "product_model": "catalog", "program_form_config_model": "learning",
    "program_model": "learning", "review_audit_model": "moderation", "service_booking_model": "booking",
    "service_model": "catalog", "training_form_config_model": "learning", "training_model": "learning",
}

# First matching prefix wins (module file name without .py), for services/, repository/, schemas/, core/, utils/, tasks/.
PREFIX_RULES = [
    # shared, domain-free
    ("common_schema", None), ("query_utils", None), ("response_mappers", None), ("virus_scan", None),
    ("html_sanitize", None), ("money", None), ("openapi", None), ("middleware", None), ("token_blacklist", None),
    ("socket_schema", "communication"),
    # identity
    ("address", "identity"), ("auth", "identity"), ("system", "identity"),
    # booking before service/catalog
    ("service_booking", "booking"),
    # catalog
    ("attribute", "catalog"), ("enterprise", "catalog"), ("location", "catalog"), ("product", "catalog"),
    ("service_service", "catalog"), ("service_repo", "catalog"), ("service_schema", "catalog"), ("catalog", "catalog"),
    ("onboarding", "catalog"), ("search", "catalog"),
    # events
    ("event", "events"), ("form_common", "events"), ("calendar", "events"),
    # learning
    ("training", "learning"), ("program", "learning"), ("ebook", "learning"), ("media", "learning"),
    ("assessment", "learning"), ("my_trainings", "learning"),
    # commerce
    ("cart", "commerce"), ("order", "commerce"), ("payment", "commerce"), ("currency", "commerce"), ("tax", "commerce"),
    ("invoice", "commerce"), ("refund", "commerce"), ("settlement", "commerce"), ("finance", "commerce"),
    ("reconciliation", "commerce"),
    # communication
    ("chat", "communication"), ("conversation", "communication"), ("group", "communication"), ("message", "communication"),
    ("attachment", "communication"), ("typing", "communication"), ("presence", "communication"), ("device", "communication"),
    ("provider_assignment", "communication"), ("socket", "communication"), ("transcription", "communication"),
    ("speech", "communication"), ("firebase", "communication"), ("bravo", "communication"),
    # notifications
    ("notification", "notifications"), ("platform_notification", "notifications"), ("user_notification", "notifications"),
    # content
    ("blog", "content"), ("uploads", "content"), ("generic_upload", "content"),
    # meetings
    ("meeting", "meetings"),
    # moderation
    ("review", "moderation"), ("admin", "moderation"),
]

PACKAGE_DOMAIN = {
    "app.services.meeting_providers": "meetings",
    "app.services.payment_gateways": "commerce",
    "app.realtime": "communication",
    "app.form_builder": None,
    "app.celery_app": None,
    "app.main": None,
    "app.api.v1.router": None,
}
OVERRIDES = {
    "app.tasks.event_tasks": "events",
    "app.tasks.notification_tasks": "notifications",
    "app.utils.tax_ids": "commerce",
    "app.core.catalog_access": "catalog",
    "app.core.payment_access": "commerce",
    "app.core.blog_access": "content",
    "app.services.review_common": None,   # pure review policy (no database): every review owner copies it
    "app.services.review_access": None,
    "app.services.notification_triggers": "events",  # composes event-registration notifications from event data
    "app.services.catalog_reviews": "catalog",
    "app.services.review_notifications": None,  # per review kind: each owner keeps its part
    "app.services.review_admin_service": "moderation",
    "app.services.review_audit": "moderation",
    "app.schemas.review_schema": "moderation",
    "app.services.attachment_storage": None,
    "app.services.firebase_push_service": None,
    # pure event helpers (no database): copied into every service that formats events
    "app.utils.event_utils": None, "app.utils.event_modules": None, "app.utils.event_accommodation": None,
    "app.utils.event_meals": None, "app.utils.event_default_ticket": None, "app.utils.event_payments": None,
    "app.utils.event_currency": None,  # FCM adapter: each sender owns a copy
    "app.services.generic_upload_service": None,  # file storage helpers on the shared upload volume
    "app.services.calendar_service": None,        # pure .ics generation
    "app.services.form_common_view": None,        # common form library: each owner keeps its own branch
    "app.services.form_common_write": None,
    "app.services.media_service": "learning",
    "app.schemas.payment_report_schema": "commerce",
}

_ENDPOINT_OWNER = {m["module"]: m["service"] for m in json.loads((V2 / "tools" / "ownership.json").read_text(encoding="utf-8"))["mounts"]}


def domain_of_module(module: str):
    if module in OVERRIDES:
        return OVERRIDES[module]
    for pkg, dom in PACKAGE_DOMAIN.items():
        if module == pkg or module.startswith(pkg + "."):
            return dom
    parts = module.split(".")
    if module.startswith("app.api.v1.endpoints."):
        return _ENDPOINT_OWNER.get(parts[-1])
    if module.startswith("app.models."):
        return MODEL_DOMAIN.get(parts[-1])
    name = parts[-1]
    for prefix, dom in PREFIX_RULES:
        if name.startswith(prefix):
            return dom
    return None


def _tables():
    out: dict[str, list[str]] = {s: [] for s in set(MODEL_DOMAIN.values())}
    for f in (APP / "models").glob("*.py"):
        dom = MODEL_DOMAIN.get(f.stem)
        if not dom:
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "__tablename__" for t in node.targets):
                if isinstance(node.value, ast.Constant):
                    out[dom].append(node.value.value)
    return {k: sorted(v) for k, v in out.items()}


TABLES_BY_SERVICE = _tables()
