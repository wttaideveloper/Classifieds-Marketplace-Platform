# Event feedback: access, responses and notifications

"Feedback" here is the feedback form an attendee sends with `POST /events/{event_id}/feedback` (questionnaire `answers`, an optional `rating` and `comment`). A review (stars and a comment written in the Ratings & Reviews section) is a different thing; see `reviews-and-ratings-api.md`.

## Who can read it

| Call | Enterprise Admin / provider of the owning business | Super Admin (active) | Anyone else |
|---|---|---|---|
| `GET /events/{event_id}/feedback` | yes | yes | no |
| `GET /events/{event_id}/reports?type=feedback&format=json` | yes | yes | no |
| `GET /events/{event_id}/reports?type=<any other>` | yes | **no**, 403 "A Super Admin can read the feedback report only" | no |

## What each role gets when it cannot read it, or there is nothing

| Case | Status | Body |
|---|---|---|
| Not logged in | 401 | `{"detail": "Not authenticated"}` |
| A customer or any role that is not staff | 403 | `{"detail": "Not authorized"}` |
| An Enterprise Admin or provider of another business | 403 | `{"detail": "Not authorized for this tenant"}` |
| An inactive Super Admin | 403 | `{"detail": "Not authorized"}` |
| A Super Admin asking for a report other than feedback | 403 | `{"detail": "A Super Admin can read the feedback report only"}` |
| Unknown event | 404 | `{"detail": "Event not found"}` |
| An event with no feedback (list) | 200 | `[]` |
| An event with no feedback (report) | 200 | see below |

## Samples

`GET /events/{event_id}/feedback`
```json
[
  {
    "id": "811e14fa-e0d2-4771-838b-3b2bad6ac308",
    "event_id": "ca6c5158-cdc5-49fb-af6a-dfd5753772dd",
    "participant_name": "Asha Rao",
    "participant_email": "asha@example.com",
    "form_id": "post-event",
    "answers": { "overall": "Excellent", "would_recommend": true, "topics": ["Yoga", "Breathing"] },
    "rating": "5",
    "comment": "Great sessions",
    "is_review": false,
    "created_at": "2026-10-19T05:29:51.837880"
  }
]
```
Newest first. `participant_name` is the name on the attendee's registration, `null` when there is none. `answers` is exactly what the app sent, any JSON. `rating` is text. Dates are UTC without a "Z".

`GET /events/{event_id}/reports?type=feedback&format=json`
```json
{
  "event_id": "ca6c5158-cdc5-49fb-af6a-dfd5753772dd",
  "type": "feedback",
  "data": {
    "total_feedbacks": 1,
    "total_reviews": 0,
    "approved_reviews": 0,
    "average_rating": null,
    "feedbacks": [
      {
        "id": "811e14fa-e0d2-4771-838b-3b2bad6ac308",
        "participant_name": "Asha Rao",
        "form_id": "post-event",
        "answers": { "overall": "Excellent", "would_recommend": true, "topics": ["Yoga", "Breathing"] },
        "rating": "5",
        "comment": "Great sessions",
        "created_at": "2026-10-19T05:29:51.837880"
      }
    ]
  }
}
```
`feedbacks` has the 20 newest forms; `total_feedbacks` is the full count (use the list call for all of them). `total_reviews`, `approved_reviews` and `average_rating` are about reviews: the average is of approved reviews only and is `null` when there are none. `format` accepts only `json`.

Empty:
```json
{ "event_id": "ca6c5158-…", "type": "feedback",
  "data": { "total_feedbacks": 0, "total_reviews": 0, "approved_reviews": 0, "average_rating": null, "feedbacks": [] } }
```

## Notification: `event_feedback_submitted`

Sent after the feedback is saved, to **both** the event's owning Enterprise Admin(s) and the Platform Super Admin(s) (a Super Admin who is also an owner of the business gets it once). In-app and push. Review submissions are not announced this way; they use `review_submitted`.

Title "New event feedback", text `Asha Rao sent feedback on "Wellness Summit".` (or "An attendee ..." when no name is on file). The text never contains the comment.
```json
{
  "category": "event_feedback_submitted",
  "event_id": "ca6c5158-…", "item_id": "ca6c5158-…", "entity_type": "event", "entity_id": "ca6c5158-…",
  "event_title": "Wellness Summit", "item_name": "Wellness Summit",
  "feedback_id": "811e14fa-…",
  "attendee_name": "Asha Rao",
  "rating": 5,
  "form_id": "post-event",
  "comment": "Great sessions",
  "submitted_at": "2026-10-19T05:29:51.837880"
}
```
- The event id is sent as `event_id`, `item_id` and `entity_id` (all the same). Use `event_id` or `entity_type` + `entity_id`.
- `comment`, `attendee_name`, `rating` and `form_id` are left out when there is none. The questionnaire `answers` are not in the notification: open the feedback with `feedback_id` or the list call.
- `comment` is in the in-app feed and the realtime `notification` event only. It is never put in the push payload.
- `submitted_at` is UTC without a "Z".

## Other change

The attendee saved with a feedback form is the logged-in user (their token's email and user id). A `participant_email` in the body is used only when the token has no email. This also keeps `feedback_status` on `GET /events/my/registrations` accurate.
