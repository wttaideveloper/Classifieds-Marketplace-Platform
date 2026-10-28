# Assessment attempt history and review

All paths below are under `/api/v1/trainings/{trainingId}`. The attempt endpoints require authentication, an active, unexpired enrolment, and a training available to learners. An assessment outside that training or another learner's attempt returns 404; lack of enrolment returns 403.

`GET /assignments` retains its existing objects. Assessment objects add `attempts_allowed`, `attempts_made`, `latest_attempt_id`, `latest_score`, `latest_total_points`, `latest_score_percent`, `latest_passed`, and `can_review`. No attempt gives null latest values and `can_review: false`. `can_review` is true if any attempt's grading details are released, even if the latest attempt is pending or unreleased. Learner question definitions do not include answer keys or explanations.

`GET /assignments/{assessmentId}/attempts` returns an array ordered by submission time and ID descending. Each item is `{ "id": "…", "attempt_number": 1, "submitted_at": "…", "score": 2, "total_points": 3, "score_percent": 66, "passed": false, "status": "evaluated", "can_review": true }`. Percentage is the integer floor of `score * 100 / total_points`; it is null when score or total is unavailable or total is zero. Zero scores remain zero. Pending and unreleased results have null score, percentage and pass/fail. `status` is `pending` or `evaluated`; `can_review` says whether grading details may be shown.

`GET /assignments/{assessmentId}/attempts/{attemptId}` returns those fields plus `assignment_id`, `assignment_title`, `attempts_allowed`, `passing_score`, `pass_percent`, `feedback`, and `questions`. Each question has `id`, `question_text`, `question_type`, `points`, `points_awarded`, `options` (`id` and `label`), `user_answer`, `correct_answer`, `is_correct`, and `explanation`. Questions and options are in snapshot order. Choice values are returned as IDs; a stored label is mapped only if it unambiguously identifies one option. Multiple selections are comma-separated and boolean values are `true` or `false`. Ambiguous values return null. Free-text answers remain free text.

New submissions preserve the assessment definition, original answers, total, pass threshold, attempt number, and automatic per-question results. They preserve the configured question order, not a randomized presentation order: the submit payload does not carry the presentation order. Manual grading updates the overall result and feedback; individual manual points remain null because the existing grading API supplies only an overall score. Pending attempts expose the learner's own answers but no answer key, correctness, score or feedback. `immediate` evaluated results are available; `scheduled` results are available at their saved release time; `manual` results remain hidden because the existing system has no separate release action. The older assessment review route applies the same learner release check.

Attempts created before snapshots existed remain listed, but their original questions, total, grading status and option mappings cannot be safely recovered from today's mutable assessment. They are shown as pending/unknown with null result fields and an empty question list, and are not regraded on reads. The legacy review route does not release those records to learners. An administrator may need to determine how historical results should be handled.

The existing submit payload remains unchanged. Concurrent submissions for an enrolled learner serialize on their enrolment row before the attempt limit is checked. The PostgreSQL migration is additive; deploy using the repository's `alembic upgrade heads` procedure.

## Staff: list every learner's attempts at an assessment

`GET /api/v1/trainings/{trainingId}/assessments/{assessmentId}/submissions`

**Who can call it:** the training's Enterprise Admin (`admin`) and Provider, limited to trainings of their own business, and a Super Admin. A learner (customer) gets 403, a missing token 401, an admin of another business 404, an unknown assessment 404. These are the same rules as the manual grading endpoint.

**Query**

| Parameter | Meaning |
|---|---|
| `page`, `page_size` | 1-based page (default 1). `page_size` 1 to 100 (default 20) |
| `status` | `all` (default), `pending` (waiting for manual grading) or `evaluated` |
| `search` | learner name or email, partial, any case |
| `from_date`, `to_date` | submitted at or after / at or before this time, inclusive. UTC ISO 8601: `2026-10-26T07:22:00Z`, with an offset (`+05:30`, converted), without an offset (read as UTC) or a bare date (`2026-10-26`, which covers that whole day). Not a time: 422. `from_date` after `to_date`: 422 |
| `order` | `newest` (default) or `oldest`, by submission time |

**Populated response**

```json
{
  "assessment_id": "13f10295-cb03-4b7d-8378-aeb43934690a",
  "assessment_title": "Module 1 Quiz",
  "items": [
    {
      "submission_id": "b1f0c0de-6d0e-4a52-9c1b-2a9d5f6e7a10", "attempt_id": "b1f0c0de-6d0e-4a52-9c1b-2a9d5f6e7a10",
      "learner_name": "Ben Carter", "learner_email": "ben@example.com", "attempt_number": 1,
      "status": "pending", "needs_manual_grading": true,
      "score": 2, "total_points": 5, "score_percent": 40, "passed": null,
      "submitted_at": "2026-10-22T10:00:00", "is_legacy": false
    },
    {
      "submission_id": "5d7e3c4a-8a41-4b8e-a0d2-61f3c9b2e4d1", "attempt_id": "5d7e3c4a-8a41-4b8e-a0d2-61f3c9b2e4d1",
      "learner_name": "Asha Rao", "learner_email": "asha@example.com", "attempt_number": 2,
      "status": "evaluated", "needs_manual_grading": false,
      "score": 4, "total_points": 5, "score_percent": 80, "passed": true,
      "submitted_at": "2026-10-21T10:00:00", "is_legacy": false
    }
  ],
  "pagination": { "total": 4, "page": 1, "page_size": 20, "total_pages": 1 },
  "status_counts": { "all": 4, "pending": 2, "evaluated": 2 }
}
```

**Empty response** (nothing submitted, or nothing matches the filters)

```json
{
  "assessment_id": "13f10295-cb03-4b7d-8378-aeb43934690a",
  "assessment_title": "Module 1 Quiz",
  "items": [],
  "pagination": { "total": 0, "page": 1, "page_size": 20, "total_pages": 0 },
  "status_counts": { "all": 0, "pending": 0, "evaluated": 0 }
}
```

**Fields**

- `submission_id` and `attempt_id` are the same value: the attempt's id. It is the `{sid}` of `GET .../assessments/{aid}/submissions/{sid}/review` and `POST .../submissions/{sid}/grade` (the staff endpoints), and the `attempt_id` of the learner's own `GET /assignments/{aid}/attempts/{attemptId}`. Staff cannot open the learner endpoint (it only returns the caller's own attempts); they use the staff review and grade endpoints.
- `learner_name` is the name on the learner's enrolment (null if none is found). `learner_email` is the email the attempt was submitted with.
- `attempt_number` is 1 for the learner's first attempt at this assessment.
- `status` is `pending` or `evaluated`, and `needs_manual_grading` is true exactly while it is `pending`.
- `score` is the points scored. While `pending` it is only the part that was marked automatically, so show it as partial. `total_points` is the assessment's total when the attempt was made. `score_percent` is the whole number rounded down, null when the score or total is missing or the total is 0.
- `passed` is null while `pending` (the result is not final) and `true` or `false` once `evaluated`.
- Unlike the learner's view, staff see scores whether or not the result has been released to the learner.
- `is_legacy: true` is an attempt saved before result snapshots existed. Its grading status, score and total cannot be recovered, so it shows `status: "pending"`, `needs_manual_grading: true`, and null `score`, `total_points`, `score_percent` and `passed`. The staff grade endpoint still works on it.
- `status_counts` are the totals for the same `search` and dates but ignoring `status`, so the Pending and Evaluated tabs can show their numbers. Pagination `total` follows the `status` filter.
- `submitted_at` is UTC and has no offset in its text.
