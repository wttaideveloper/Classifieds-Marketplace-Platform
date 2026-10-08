# Lesson attendance completes the lesson

When an admin or provider marks a learner **attended** on a lesson, either by ticking the roster or by scanning their QR code, that lesson is now marked complete for the learner, and their progress totals are updated. This is done by the attendance APIs themselves. The frontend does not have to call anything else.

Applies to trainings and courses (`/trainings/...` and `/courses/...` are the same endpoints).

## The rules

1. Only `attended` completes a lesson. `absent` and `not_marked` never do.
2. Repeating it changes nothing. Marking `attended` again, or scanning the same QR again, does not duplicate anything and does not move `completed_at`.
3. Reversing attendance (`attended` to `absent` or `not_marked`) undoes only the completion that attendance created. If the learner completed the lesson on their own, it stays complete.
4. If the learner completes a lesson on their own after attendance completed it, the completion becomes theirs and a later reversal no longer undoes it.
5. If undoing a completion means the learner no longer meets the course completion rule, `completed_at` and `certificate_url` are cleared. A certificate that depended only on that attendance is withdrawn.
6. Quiz and exam lessons are not completed by attendance. They are completed by submitting the assessment, which is what `/content` reads for them. The attendance status is still saved for them.
7. A scan that is repeated on an already-attended learner (`result: "already_attended"`) also makes sure the lesson is complete. This fixes attendance that was recorded before this change.

## APIs that do this

| API | Who | Completes the lesson when |
|---|---|---|
| `POST /trainings/{training_id}/lessons/{lesson_id}/attendance/roster` | admin, provider | a record has `status: "attended"` |
| `POST /trainings/{training_id}/lessons/{lesson_id}/attendance/scan` | admin, provider | the scan is accepted |
| `POST /trainings/{training_id}/lessons/{lesson_id}/attendance` (self check-in, or `{"qr_code": ...}` by an admin) | learner / admin | the check-in is recorded (not reversible) |
| `POST /trainings/{training_id}/live-sessions/{session_id}/attendance` | learner / admin | the check-in is recorded (not reversible) |

The last two are the older check-in endpoints. They already marked the lesson complete, but did not update the learner's overall percent. They now do.

Not changed: `POST /trainings/{training_id}/enrolments/check-in` and the batch check-in. They check the learner into the whole training, not into a lesson, so there is no lesson to complete.

## Response contract

The roster endpoints (`GET` and `POST .../attendance/roster`) return one object per participant. The scan endpoint returns the same fields for the one scanned participant, plus `result` and `message`. Three fields are new on each participant.

```json
{
  "training_id": "94aa1aaa-2222-458a-80b3-f80d37137ec2",
  "lesson_id": "36c1bb92-d7ac-40cf-ba78-dde0478ab44d",
  "lesson_title": "Day 1 at the venue",
  "lesson_type": "venue",
  "participants": [
    {
      "enrolment_id": "6d2c0b2e-1f8a-4c9e-a3b1-2f4a9a1d7c10",
      "participant_name": "Alice",
      "participant_email": "alice@example.com",
      "enrolment_status": "enrolled",
      "status": "attended",
      "marked_by": { "id": "…", "name": "Admin One", "email": "admin@example.com" },
      "marked_at": "2026-10-08T14:05:11.123456",

      "is_completed": true,
      "completed_by_attendance": true,
      "progress": {
        "overall_percent": 25.0,
        "lessons_done": 1,
        "total_lessons": 4,
        "mandatory_done": 0,
        "mandatory_total": 1,
        "completed_at": null
      }
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `is_completed` | The learner has completed this lesson. Same value as `is_completed` for this lesson in `GET /content`. |
| `completed_by_attendance` | `true` when attendance created the completion, so un-marking them will undo it. `false` when the learner completed it themselves, or it is not complete. |
| `progress` | The learner's course totals after this change. `overall_percent` is 0 to 100, `completed_at` is null until the course is complete. |

Scan response:

```json
{
  "enrolment_id": "…", "participant_name": "Alice", "participant_email": "alice@example.com",
  "enrolment_status": "enrolled", "status": "attended", "marked_by": { "…": "…" }, "marked_at": "…",
  "is_completed": true, "completed_by_attendance": true,
  "progress": { "overall_percent": 25.0, "lessons_done": 1, "total_lessons": 4, "mandatory_done": 0, "mandatory_total": 1, "completed_at": null },
  "result": "marked",
  "message": "Alice marked attended"
}
```

`result` is `marked` for a new scan and `already_attended` for a repeat. Errors are unchanged.

## What the learner's app sees

`GET /trainings/{id}/content` shows `is_completed: true` for the lesson straight away, and `GET /trainings/{id}/progress` shows the new totals. For live and venue lessons `attended_at` is set as before.

## Deploy

One new column, `training_progress.attendance_completed_lessons`, remembers which lessons attendance completed. Run `alembic upgrade c7e2a9d4b1f6` before deploying the code.

Existing completions are not touched. The new column starts empty, so nothing that is already complete is treated as attendance-created, and a later reversal will not undo it.
