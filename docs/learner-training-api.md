# Learner training endpoints

All routes require authentication. Learner identity comes from the token.

- `GET /api/v1/me/trainings`: enrolment card array; supports optional `status`. Includes `qr_code`, `qr_image_base64`, `delivery_mode`, `progress_percent`, and enrolment `status`.
- `GET /api/v1/trainings/{id}`: training detail. Non-enrolled learners receive no protected lesson content, assessments, assignments, meeting credentials, QR credentials, or downloadable documents. Enrolled lessons use the existing release and assessment gates.
- `POST /api/v1/me/trainings/{id}/qr-show`: returns `training_id`, `qr_code`, and `qr_image_base64` for the caller's active enrolment. The image encodes the enrolment code accepted by the door scanner. Missing enrolment returns 403; expired access returns 410.
- `POST /api/v1/trainings/{id}/reviews`: accepts `rating` and optional `comment`. The legacy `participant_email` input is ignored in favor of the signed-in identity. Unenrolled users receive HTTP 403 with `{"detail":"Verified reviews only — must be enrolled to review"}`.
- `POST /api/v1/me/trainings/{id}/wishlist`: toggles saved state, returning `training_id`, `wishlisted` (boolean), and `message`.

QR images are PNG data URIs (`data:image/png;base64,...`), or null when no code exists. Existing `/trainings/my/enrolments` and separate wishlist add/delete endpoints remain available.
