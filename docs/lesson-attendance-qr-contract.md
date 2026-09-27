# Lesson attendance QR contract

GET /api/v1/trainings/{training_id}/content returns a distinct, stable QR for each
unlocked live/venue lesson eligible for venue QR display under the existing
delivery-mode rules. The code is scoped to the learner's enrolment and lesson:
`<enrolment_qr>:<lesson_id>`. The image is a PNG data URI encoding that exact
string. The same fields appear in sections[].lessons[] and sections[].items[].
Different days must be represented by different lesson IDs; changing the
schedule of an existing lesson does not rotate its code.

Example:
```json
{
  "id": "day-1-lesson-id",
  "type": "venue",
  "qr_code": "DD61662A-8D8:day-1-lesson-id",
  "qr_image_base64": "data:image/png;base64,...",
  "is_attended": false,
  "attended_at": null
}
```

Use the existing admin scan endpoint:
POST /api/v1/trainings/{training_id}/lessons/{lesson_id}/attendance/scan
with {"qr_code": "<exact scanned value>"}.
The scanner can obtain the lesson ID from the portion after the first colon.
The backend verifies that it matches the URL and that the lesson and enrolment
belong to the requested training. A mismatch returns 400 with
{"detail": "QR code belongs to a different lesson"}.

Existing tenant/manager authorization, cancelled/expired enrolment rejection,
lesson availability rules and scan idempotency still apply. Legacy enrolment
QRs remain accepted when an admin explicitly selects a lesson.

The existing POST .../lessons/{lesson_id}/attendance and
POST .../live-sessions/{session_id}/attendance also accept scoped codes matching
their target ID. QR-based attendance requires manager authorization. When a QR
is supplied, its enrolment determines the participant, including when the body
also contains participant_email.

After an admin scan, a fresh GET /content returns is_attended=true and
attended_at for that lesson only. Admin roster marks take precedence over older
self-check-in records; marking absent/not_marked clears the displayed attendance.
Training/section-level QR contracts are unchanged. No new endpoint or database
migration is required. This change does not enable QR display for locked,
non-enrolled, recorded or online-only content.
