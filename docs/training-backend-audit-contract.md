# Training/Course backend audit fixes

## Ownership

Training-specific management routes now use `require_training_manager`, backed by
the repository's `require_training_owner`. This includes curriculum sections,
lessons, media removal, topics, ordering, assessments, assignments and other
existing training-specific manager operations. Both `/api/v1/trainings` and
`/api/v1/courses` use the protected router.

The authenticated tenant is resolved through the existing
`resolve_auth_tenant_id_with_db` helper, including enterprise claims and WebAuth
cookie fallback. Ownership is checked against `Enterprise.tenant_id` and
`Training.tenant_id`. Missing or conflicting ownership fails closed.

- Same-tenant admin/provider: allowed.
- Cross-tenant admin/provider: HTTP 403, `{"detail":"Not authorized for this tenant"}`.
- Active `super_admin`: cross-tenant management allowed.
- Missing training: HTTP 404, `{"detail":"Training not found"}`.
- Nested section/lesson identifiers are resolved within the authorized training.

The generic repository lookup remains available for public and learner reads;
management callers use the explicit authorization wrapper.

## Enrolment CSV

`GET /api/v1/trainings/{training_id}/enrolments/export`

Also available under `/api/v1/courses/{training_id}/enrolments/export`.

- Requires the ownership rules above, checked again in the export service.
- HTTP 200, `Content-Type: text/csv; charset=utf-8`.
- Download filename: `training_{training_id}_enrolments.csv`.
- Columns: `id,training_id,participant_name,participant_email,status,group_enrol,created_at`.
- No enrolments: header-only CSV, HTTP 200.
- CSV quoting handles commas and newlines; potentially executable spreadsheet
  cells are prefixed with an apostrophe.

## Checkout

`POST /api/v1/trainings/{training_id}/checkout` retains its existing request and
successful response structure. Checkout now calls the normal enrolment service
with deferred commit, then commits the enrolment and order together.

Existing enrolment rules apply, including publication status, enrolment window,
coupon validation, duplicate enrolment and capacity. Their existing HTTP 400
error details are preserved. Enrolment/order insertion failures roll back the
transaction and propagate instead of returning a successful checkout.

Normal `/enrol` and `/enroll` retain their existing commit behavior. Checkout
creates one enrolment for the named participant, as before; this change does not
introduce a group-checkout contract.

## Moderation history

Valid transitions through `update_training_status_service` append a history
entry in the same commit as the status change:

```json
{
  "action": "approved",
  "previous_status": "pending_approval",
  "new_status": "approved",
  "reason": "Review note",
  "actor_id": "authenticated-user-id",
  "actor_email": "reviewer@example.com",
  "actor_role": "super_admin",
  "at": "ISO-8601 timestamp"
}
```

Missing actor information/reason is null. Existing entries are retained and
returned by `GET /{training_id}/moderation-history`. The transition map is
unchanged: `draft -> published` remains rejected and creates no history entry.

## Dynamic form management

Event, Training and Program form configuration routers are explicitly labeled
Super Admin in the existing architecture. Their mutating routes now use
`require_form_configuration_super_admin`, which rejects the ordinary-admin and
development fallbacks allowed by the shared Super Admin dependency.

This applies to create, patch, delete, publish, activate, deactivate, retire and
assignment replacement on all existing aliases of those routers. Ordinary
admin/provider identities receive HTTP 403. Platform Super Admin identity still
uses the existing identity resolver. Read routes and active-form consumption
retain their existing authorization.

## Root causes and verification

The original management handlers checked role without ownership. The enrolment
CSV route was absent. Checkout committed the order before a separate enrolment
attempt whose exceptions were swallowed. Status transitions omitted the history
helper. Form mutation routes used the permissive builder dependency.

Regression tests are in `tests/test_training_backend_audit.py` and
`tests/test_form_configuration_mutation_auth.py`. They exercise HTTP dependency
chains and real SQLAlchemy commits/reloads, including injected insert failures.
Database tests use SQLite; production PostgreSQL concurrency and deployment
were not exercised. No database migration is required.

### Results

- Before changes: 229 passed, 8 failed, 330 deselected.
- After changes: 324 passed, the same 8 failed, 330 deselected.
- All 95 added regression cases passed; no new failures in the selected suite.
- Existing failures: four `recorded` delivery-mode cases in
  `test_training_curriculum_contract.py`, and four prerequisite/content-locking
  cases in `test_training_secure_content_v2.py`. These were left unchanged.
- Command: `python -m pytest tests -q --tb=short --disable-warnings -k 'training or form_config'`.
- Python compilation of `app` and both new test files passed.
- `git diff HEAD --check` passed.

### Changed files

- `app/repository/training_repo.py`: ownership lookup.
- `app/api/v1/endpoints/training.py`: management guard and export route.
- `app/services/training_service.py`: CSV generation, atomic checkout and history.
- `app/core/dependencies.py`: strict form-management guard.
- `app/api/v1/endpoints/event_form_config_admin.py`: mutation authorization.
- `app/api/v1/endpoints/training_form_config_admin.py`: mutation authorization.
- `app/api/v1/endpoints/program_form_config_admin.py`: mutation authorization.
- `tests/test_training_backend_audit.py`: database and HTTP regressions.
- `tests/test_form_configuration_mutation_auth.py`: form authorization regressions.
- This contract document.

At verification, the seven application files and two test files were staged
(466 insertions, 110 deletions); this document was untracked. No commit or
deployment was performed by this task.
