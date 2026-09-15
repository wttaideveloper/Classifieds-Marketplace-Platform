# Training / Course curriculum contract

## Endpoints and roles

All paths below have the `/api/v1` prefix. `/courses` is an alias of `/trainings` and uses the same handlers.

| Endpoint | Purpose | Success |
| --- | --- | --- |
| `POST /trainings/` | Admin/provider creates a Training with the full curriculum | 201 TrainingResponse |
| `PUT /trainings/{id}` | Admin/provider partially updates Training fields; supplied arrays replace those arrays | 200 TrainingResponse |
| `GET /trainings/{id}` | Authenticated curriculum/detail view; learner fields are gated | 200 TrainingDetailResponse |
| `GET /trainings/{id}/content` | Enrolled learner content plus progress, locks and submissions; admin/provider staff can inspect | 200 content object |
| `POST /trainings/{id}/sections` | Add a section, optionally with inline items | 201 section |
| `PUT /trainings/{id}/sections/{section_id}` | Update section and curriculum values | 200 section |
| `POST /trainings/{id}/sections/{section_id}/lessons` | Add an item, including inline quiz/task | 201 item |
| `PUT /trainings/{id}/sections/{section_id}/lessons/{lesson_id}` | Update an item | 200 item |

The existing active Training Form resolution and required-field validation still apply to creation.

## Shared authoring structure

Create/update now accept `sections`, `assessments`, `assignments`, and `notes_pdf_url` directly. `documents` and `notes_documents` preserve uploaded-file metadata. For example:

```json
{
  "enterprise_id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "First Aid",
  "category": "Safety",
  "delivery_mode": "online",
  "documents": [{"id":"handbook","title":"Handbook","type":"pdf","url":"https://cdn.example.com/handbook.pdf","size":"420 KB","downloadable":true,"visibility":"enrolled"}],
  "notes_pdf_url": "https://cdn.example.com/notes.pdf",
  "sections": [{
    "id": "session-1",
    "title": "Scene safety",
    "schedule": "2026-09-20T19:00:00+05:30",
    "items": [{
      "id": "live-1",
      "type": "live",
      "title": "Live class",
      "meeting_link": "https://meet.example.com/room",
      "join_meta": "Join five minutes early",
      "duration_minutes": 60,
      "is_mandatory": true
    }, {
      "id": "quiz-1",
      "type": "quiz",
      "title": "Scene safety quiz",
      "is_mandatory": true,
      "assessment": {
        "id":"assessment-1",
        "type":"quiz",
        "pass_percent":70,
        "questions":[{
          "id":"q1",
          "question_text":"What comes first?",
          "question_type":"single_choice",
          "points":1,
          "options":[{"id":"a","label":"Check scene safety"},{"id":"b","label":"Run in"}],
          "correct_answer":"a"
        }]
      }
    }]
  }]
}
```

Example URLs illustrate the contract and must be replaced with actual uploaded media or meeting URLs. This change does not create provider meetings.

### Names, aliases and persistence

- New item types: `topic`, `video`, `live`, `venue`, `pdf`, `notes`, `quiz`, `assignment`.
- Existing legacy types, including `text` and `exam`, remain accepted and retain their identifiers. `other` is normalized using explicit fields (assessment/video/meeting/venue/PDF), or becomes `topic`; titles are not used to invent content.
- Either `sections[].items` or `sections[].lessons` is accepted. Responses include **both**, with identical values. If both are supplied in a write, they must agree.
- Array position determines contiguous `order: 1..N`. IDs are preserved or generated when absent. Item IDs must be unique across a Training.
- `content` and `detail` are retained. `video_url` falls back to `content_url`, and `join_url` to `meeting_link`, consistently on reads.
- Section schedule accepts an ISO timestamp or the existing schedule object. Recorded schedules are null.
- Inline `assessment`/`assignment` objects are stored in the Training's corresponding collections and linked by `assessment_id`/`assignment_id`. Later submissions use those IDs. Explicit top-level collections are also accepted.
- Runtime state (`is_completed`, submission results, scores, feedback, locks) is calculated from backend state; submitting it as authoring data does not mark a learner complete.
- Choice questions preserve real `{id,label}` options. `mcq`/`multiple_choice` aliases normalize to `single_choice`. Choice questions with fewer than two options are rejected on curriculum writes. Missing options are never fabricated.
- YouTube search result URLs are rejected on new writes and withheld on normalized reads. No network check certifies that an arbitrary supplied URL exists.
- Existing incomplete records require an Admin update with real content, quiz choices, and task details. There is no invented-data backfill.

## Delivery modes

| Mode | Venue/QR | Meeting | Schedule/media |
| --- | --- | --- | --- |
| `physical` | Venue required; `check_in=true`; server-generated training pass/QR | Meeting fields null | Venue items with address/check-in window |
| `online` | Venue and QR null; `check_in=false` | Top-level meeting link or a live item's link required | Live items with supplied join details |
| `hybrid` | Venue required; `check_in=true` | Top-level link or a live item's link | Mix live and venue items; participant QR only on venue items |
| `recorded` | Venue and QR null; `check_in=false` | Meeting fields null; live/venue authoring rejected | Videos/topics/PDFs/notes/quizzes/tasks; schedules null |

`self_paced` remains supported as the recorded-mode alias without renaming existing stored records. `blended` and `instructor_led` retain legacy support.

Training `qr_payload` is the existing shared self-check-in payload. Learner `qr_code` is a **per-enrolment** code accepted by the door scanner. They are different credentials and are not interchangeable. `/content` exposes the learner's code at the top level and on unlocked venue items; it is null for online/recorded. `/me/trainings/{id}/qr-show` returns 400 for online/recorded/self-paced modes.

## Detail versus learning content

The common curriculum fields remain available across responses, but caller permissions determine their values:

- **Admin create/update/detail:** full authored curriculum, with inline assessment/task objects and top-level collections. Authoring endpoints can return answer keys.
- **Unenrolled learner detail:** section/item metadata and explicit preview content. Protected content, meeting credentials, QR credentials, task instructions, and quiz questions are withheld. The section list is not empty merely because the caller is unenrolled.
- **Enrolled learner detail:** secure sections, with the same locks used by `/content`.
- **Learning `/content`:** all visible curriculum items plus `is_locked`, `is_completed`, `completed_at`, assessment submission state and assignment submission state. Draft items remain hidden according to the existing rules.

Locked media URLs, video aliases, meeting links, join metadata and QR credentials are null. Locked quiz/task payloads are null. Learner question responses never include `correct_answer`. These access rules take precedence over illustrative samples that show live credentials on locked items.

Common section fields include `id`, `type`, `order`, `title`, `summary`, `schedule`, `venue`, `address`, `meeting_link`, `items`, and `lessons`; learning sections add `is_unlocked` and `unlock_hint`.

Learning top-level fields include:

```json
{
  "training_id": "training-uuid",
  "title": "First Aid",
  "primary_image": null,
  "enterprise_name": null,
  "instructor_name": null,
  "delivery_mode": "online",
  "progress_percent": 50,
  "completed_items": 1,
  "total_items": 2,
  "completed_required_items": 1,
  "total_required_items": 2,
  "completed_lessons": 1,
  "total_lessons": 2,
  "qr_code": null,
  "notes_pdf_url": "/api/v1/trainings/training-uuid/notes.pdf",
  "documents": [],
  "sections": [],
  "assessments": [],
  "assignments": []
}
```

This excerpt shows field names; section arrays are populated in actual responses. The existing `completed_lessons`/`total_lessons` counters remain for older clients. New UI should use item counters.

### Progress and scoring

- `completed_items` counts completion across visible items; assignment submission contributes to completion and embeds `is_submitted`, score/feedback, and its submission timestamp.
- `progress_percent` uses mandatory items when present; otherwise all visible items. `completed_required_items`/`total_required_items` report the mandatory subset.
- Required content gates its section quiz. Completing preceding required content and passing required quizzes unlocks subsequent sections. Legacy curricula without mandatory flags retain the existing all-content prerequisite behavior.
- Quiz completion state records submission; passing is separately required for section progression. A failed submitted quiz may count as a completed item while still blocking the next section.
- New authored quizzes record score units as points. `pass_percent` is enforced against total available points, rounded up for the passing threshold; learner `score_percent` is derived from earned/available points. Legacy score interpretation is retained for older assessments without the marker.
- `completed_at` is null when no per-item timestamp is stored. No completion dates are invented.
- Assignment `due_at` is exposed alongside the existing stored `due_date` alias. Score and feedback come from the caller's submission.

## Errors and rollout

- 400: invalid curriculum type, conflicting aliases, duplicate IDs, invalid choice options, search URL supplied as media, or incompatible recorded items.
- 403: learner is not enrolled for `/content`, or existing access gates deny content.
- 404: missing Training or referenced resources.
- 422: malformed typed request body.

Run `alembic upgrade head` before deployment. Revision `x5y6z7a8b9c0` adds `trainings.notes_pdf_url` and follows the Event template migration. Without a supplied URL, the existing generated `/trainings/{id}/notes.pdf` URL is returned. No migration has been executed against a live database in this task.

## Complete examples

These examples use sample media links and an unenrolled learner for the detail preview. Content examples show an enrolled learner who completed the first topic.

| Mode | Admin request | Detail preview | Learner content |
| --- | --- | --- | --- |
| Physical | [Create](examples/training-physical-create.json) | [Detail](examples/training-physical-detail.json) | [Content](examples/training-physical-content.json) |
| Online | [Create](examples/training-online-create.json) | [Detail](examples/training-online-detail.json) | [Content](examples/training-online-content.json) |
| Hybrid | [Create](examples/training-hybrid-create.json) | [Detail](examples/training-hybrid-detail.json) | [Content](examples/training-hybrid-content.json) |
| Recorded | [Create](examples/training-recorded-create.json) | [Detail](examples/training-recorded-detail.json) | [Content](examples/training-recorded-content.json) |

The Event template/lifecycle contract is in [event-template-lifecycle-contract.md](event-template-lifecycle-contract.md).
