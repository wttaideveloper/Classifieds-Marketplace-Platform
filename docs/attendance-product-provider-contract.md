# Live attendance and Product provider contract

## Attendance fix

`POST /api/v1/trainings/{training_id}/live-sessions/{session_id}/attendance`

The content API reads live curriculum lessons from `trainings.sections`. The
attendance handlers previously queried only `training_live_sessions`, so a
valid content lesson UUID could return 404. Two POST handlers were also
registered at the same URL, making runtime handling and OpenAPI inconsistent.

The single handler now resolves either storage format, scoped to the requested
training. Curriculum attendance is stored on the existing curriculum item;
standalone sessions continue using their attendance column. The UUID remains
unchanged. Attendance GET and CSV export resolve the same two formats.

- Learner self-attendance: no body required, or `{}`; uses authenticated email.
- Mark another participant: `{"participant_email":"learner@example.com"}`;
  requires management permission for the training.
- Target participant must have an active enrolment.
- Unknown session, wrong training or non-live curriculum item: 404.
- Invalid session UUID: 400. Unauthorized attendance: 403.
- Repeated calls preserve the first attendance timestamp without duplicates.
- Curriculum progress uses the actual content lesson UUID. Standalone sessions
  retain their existing `live:<session UUID>` progress key.
- Existing email-keyed attendance objects remain readable/exportable.
- Attendance rosters are omitted from general curriculum responses and are
  retained when the curriculum is edited through normal authoring paths.

Success response:

```json
{
  "session_id": "4fad8f33-8634-4c0c-af2e-fc75251ee3c9",
  "participant_email": "learner@example.com",
  "recorded_at": "2026-09-18T10:00:00",
  "progress_marked": true,
  "status": "success",
  "message": "Attendance recorded",
  "attendance": {
    "joined_at": "2026-09-18T10:00:00",
    "participant_email": "learner@example.com"
  }
}
```

This does not add attendance-based certificate issuance or a new schedule gate.
The reported IDs were reproduced in local database tests. A read-only lookup of
the configured PostgreSQL database failed with `OperationalError`; the actual
deployed records and deployment version could not be confirmed.

## Product provider

Product provider assignment was absent from both persistence and schemas.
It now follows the existing Service field contract:

```json
{
  "provider_user_id": "550e8400-e29b-41d4-a716-446655440020",
  "provider_name": "Provider Display Name"
}
```

Supported on:

- `POST /api/v1/products/`
- `PUT /api/v1/products/{product_id}`
- `GET /api/v1/products/{product_id}`
- Product list responses.

Both fields are nullable. `provider_user_id` is a UUID and `provider_name` is a
display label, at most 255 characters. Omitting either field on update preserves
its stored value. Send both as null to clear the assignment. Existing products
have null provider fields. The UUID is an external identity reference, matching
Services; this change does not add remote internal-user membership validation.

Apply migration `c4d5e6f7a8b9_add_product_provider.py` before deploying the new
model. PostgreSQL DDL generation was verified offline; the migration has not
been applied to the configured database.

## Chat identity and routing

Chat creation currently accepts explicit provider/participant IDs. It does not
resolve the Product or Service provider from the context fields automatically.
Use the assigned `provider_user_id` when creating/opening a conversation:

```json
{
  "context_type": "product",
  "context_id": "550e8400-e29b-41d4-a716-446655440010",
  "provider_id": "550e8400-e29b-41d4-a716-446655440020",
  "participant_ids": [
    {
      "user_id": "550e8400-e29b-41d4-a716-446655440020",
      "role": "provider"
    }
  ]
}
```

Send this to `POST /api/v1/conversations/` as the customer. Chat returns the
assignment as `assigned_provider_id`. The provider UUID must equal that person's
authenticated Chat user ID (normal token resolution uses `id`, falling back to
`sub`). There is no internal-user-to-login UUID translation in these paths.
`provider_name` is never used as a routing identity.

## Verification

New regression coverage is in `tests/test_live_curriculum_attendance.py` and
`tests/test_product_provider.py`: content-to-attendance round trip using the
reported IDs, both JSON aliases, standalone and legacy attendance, repeat calls,
enrolment/ownership guards, progress, export, provider CRUD and OpenAPI.

Related Training/Product/Chat suite: 167 passed. An earlier curriculum-contract
run had 51 passed and the four previously known `recorded` delivery-mode failures.
Python compilation and Git whitespace validation passed. No deployment or
production database changes were performed.
