# Blog engagement: likes, saved blogs, comments and replies, reports, moderation

Base path: `/api/v1/cms/blogs`. Blogs only (`kind: blog`); a News post answers `409 ENGAGEMENT_NOT_AVAILABLE` and its `engagement` is `null`.
Engagement actions use the post **id** (the list and the detail return it). Errors have `{detail, code}`.

**Status: built and tested locally (SQLite). Not committed or deployed: none of this exists on staging yet.** Needs migration `r7c0e3a6b9d4` (`alembic upgrade heads`).

## Auth

| Call | Auth |
|---|---|
| `GET /` (list), `GET /{slug}` (detail), `GET /{post_id}/comments` | Optional. Signed in: `is_liked_by_me`, `is_wishlisted_by_me`, `author.is_me`, `can_edit`, your own waiting comments |
| Like, save, comment, reply, edit, delete, report, `GET /wishlist/my` | Customer bearer token (401 without) |
| `/my/...` | Enterprise Admin of the blog's business (403 for another business) |
| `/admin/engagement/...` | Super Admin |

## Counters on the blog list and detail

`GET /api/v1/cms/blogs?kind=blog` items and `GET /api/v1/cms/blogs/{slug}` carry:

```json
"engagement": {
  "likes_count": 128,
  "comments_count": 24,
  "wishlist_count": 9,
  "is_liked_by_me": false,
  "is_wishlisted_by_me": true
}
```
`comments_count` counts published comments and replies. `is_*_by_me` are `null` when nobody is signed in.

## Likes and saves (idempotent)

| Call | Answer |
|---|---|
| `POST /{post_id}/like` | 200 |
| `DELETE /{post_id}/like` | 200 |
| `POST /{post_id}/wishlist` | 200 |
| `DELETE /{post_id}/wishlist` | 200 (also after the blog is taken down) |

```json
{ "liked": true, "wishlisted": false, "likes_count": 129, "wishlist_count": 9 }
```
Repeating a like or unlike changes nothing. A post that is not live: 404 `POST_NOT_FOUND`.

`GET /wishlist/my?page=1&page_size=20`: saved live blogs, newest save first; each item is a blog list item plus `saved_at`.

## Comments and replies

Replies are **one level deep**: a reply to a reply is attached to the top-level comment. `replies` is always nested in the list (all of a comment's visible replies, oldest first); there is no separate replies endpoint.

### `GET /{post_id}/comments?page=1&page_size=20`

Top-level comments newest first (the page counts top-level comments only).

```json
{
  "items": [
    {
      "id": "6f1c…",
      "post_id": "a2b9…",
      "parent_id": null,
      "author": { "name": "Priya S.", "is_me": false },
      "body": "Really clear breakdown.",
      "status": "published",
      "created_at": "2026-11-25T05:30:00",
      "is_edited": false,
      "edited_at": null,
      "can_edit": false,
      "can_delete": false,
      "reported_by_me": false,
      "reply_count": 1,
      "replies": [
        {
          "id": "9d0e…", "post_id": "a2b9…", "parent_id": "6f1c…",
          "author": { "name": "Alex C.", "is_me": true },
          "body": "Same here.", "status": "published", "created_at": "2026-11-25T06:20:00",
          "is_edited": true, "edited_at": "2026-11-25T06:25:00", "can_edit": true, "can_delete": true,
          "reported_by_me": false, "reply_count": 0, "replies": []
        }
      ]
    }
  ],
  "pagination": { "total": 24, "page": 1, "page_size": 20, "total_pages": 2 },
  "comments_count": 31
}
```

* `author.name` is a display name ("Priya S."): never a full name, an e-mail or a user id (health platform: readers are not identifiable). `is_me` marks your own.
* `status`: `published`; `pending_review` (only you see your own comment while the AI or a moderator checks it); `unavailable` (a removed or deleted comment that still has replies: `body` and `author.name` are `null`).

### `POST /{post_id}/comments`

```json
{ "body": "Great article, thanks!", "parent_id": null }
```
`parent_id` makes it a reply. At most 500 characters. The text is checked by the AI first:

| Status | `outcome` | Meaning |
|---|---|---|
| 201 | `published` | shown at once |
| 202 | `held_for_review` | a moderator decides; only the author sees it meanwhile |
| 202 | `pending_moderation` | the AI check was down; checked again later |
| 422 | `removed_by_ai` | against the guidelines; not posted, `comment` is `null` |

```json
{
  "outcome": "published",
  "message": "Your comment is posted.",
  "comment": { "id": "…", "parent_id": null, "author": { "name": "Priya S.", "is_me": true }, "body": "Great article, thanks!", "status": "published", "...": "same fields as above" },
  "comment_id": null
}
```
Other errors: 422 `COMMENT_EMPTY` / `COMMENT_TOO_LONG` (`max_chars`), 429 `RATE_LIMITED` (`retry_after_seconds`; 10 per 60 s per person by default), 404 `PARENT_NOT_AVAILABLE`.

### Edit, delete, report

| Call | Body | Notes |
|---|---|---|
| `PATCH /comments/{comment_id}` | `{"body": "…"}` | Author only (404 otherwise). Checked again: 200 published (`is_edited`, `edited_at`), 202 held, 422 refused (the comment keeps its old text) |
| `DELETE /comments/{comment_id}` | — | Author only, soft delete, idempotent: `{"deleted": true}` |
| `POST /comments/{comment_id}/report` | `{"reason": "unsafe_medical" \| "harassment" \| "offensive" \| "spam" \| "other", "note": "…"}` | One per person per comment (409 `ALREADY_REPORTED`); 409 `CANNOT_REPORT_OWN`. One unsafe_medical/harassment report or three of the others makes the AI judge it again |

## Enterprise Admin (own blogs only)

| Call | What |
|---|---|
| `GET /my/posts/{post_id}/engagement` | Counts only: likes, saves, comments by status, open reports, views, 30-day trend |
| `GET /my/posts/{post_id}/comments?status=` | The blog's comments with status; author is a display name only |
| `DELETE /my/comments/{comment_id}` | Remove a comment (`{"reason"}` optional); the author is told |
| `POST /my/comments/{comment_id}/request-review` | Ask a Super Admin to look at an AI removal (`{"note"}`) |

## Super Admin

`GET /admin/engagement` (overview: stats, top blogs, `search`, `status`, `date_from`, `date_to`), `GET /admin/engagement/{post_id}`, `GET /admin/engagement/comments`,
`GET /admin/engagement/ai-moderation?queue=auto_removed|awaiting_review|user_reports|ea_requests`, `POST /admin/engagement/comments/{id}/remove` and `/restore`
(`{"reason"}` required), `GET /admin/engagement/moderation-history`. Admins never edit a reader's words. The text of a removed comment is shown only here.

## Notifications

`blog_comment_removed` and `blog_comment_restored` to the author; `blog_comment_review_requested` to the Super Admins.

## Not built

Comment likes, deeper reply nesting, a separate replies endpoint, slug in engagement URLs, public author ids. See the reply to the mobile team for why and what it would take.
