# Blogs and News API

For the web and mobile developers. Base path: `/api/v1/cms/blogs`. All times are UTC.

Three kinds of caller:

- **Public / any customer**: reads live posts. No login needed.
- **Merchant**: the business's Enterprise Admin (`tenant_owner` / `tenant_admin`). Writes posts for their own business under `/my/posts`. Every post needs a Super Admin's approval before the public sees it.
- **Super Admin**: writes platform posts (live at once), decides merchant posts, manages categories, under `/admin/...`.

An Enterprise Admin cannot use the `/admin` routes. A provider or customer cannot use `/my` (403). Without a token the protected routes answer 401.

Words used here: the **summary** is the field `excerpt`, the **body** is `content`. The body is formatted text (headings, bold, italic, lists, links, images, quotes, code). Anything else (scripts, event handlers, iframes, `javascript:` links) is removed when the post is saved.

## 1. The life of a post

| Status | Meaning | Public sees it |
|---|---|---|
| `draft` | being written | no |
| `pending_approval` | sent for review | no (a live post stays up, see 1.2) |
| `changes_requested` | the Super Admin asked for edits | no |
| `rejected` | the Super Admin said no | no |
| `published` | live | yes |
| `unpublished` | taken down, can be sent again | no |
| `deleted` | removed, kept for the record | no |

### 1.1 A merchant post

1. `POST /my/posts` saves a draft.
2. `PATCH /my/posts/{id}` edits it (draft, changes requested, rejected, unpublished).
3. `POST /my/posts/{id}/submit` sends it. It needs a title, kind, category and body (422 otherwise). Status becomes `pending_approval` and the Super Admins get a notification.
4. A Super Admin decides with `POST /admin/posts/{id}/review`: `approve` (live), `request_changes` or `reject` (both need a reason, which the merchant reads in `reviewer_note` and in a notification).
5. After changes, the merchant submits again.

A post that is waiting for review cannot be edited (409).

### 1.2 Editing a live post

When a merchant edits a `published` post with `PATCH /my/posts/{id}`, the edit is sent for approval at once. The post goes to `pending_approval` but **stays live with the old version** until the edit is approved. While this is going on:

- `is_live` is `true`, `has_pending_revision` is `true`.
- The top-level fields of `GET /my/posts/{id}` and of the admin views are the **new, unapproved** version. `live_version` holds what the public sees.
- If the Super Admin approves, the edit goes live. If they reject or ask for changes, the old version stays live and the merchant can edit again and submit, or `POST /my/posts/{id}/discard-changes` to drop the edit.
- A Super Admin cannot edit the post meanwhile (409): decide the merchant's edit first.

### 1.3 Platform posts

A Super Admin creates a post with `POST /admin/posts`. `status: "published"` makes it live straight away, no approval. A draft is published later with `POST /admin/posts/{id}/publish`. Edits to a live platform post apply at once.

### 1.4 Taking down and deleting

- Merchant: `POST /my/posts/{id}/unpublish` takes their live post down. They can delete **drafts only** (`DELETE /my/posts/{id}`).
- Super Admin: `POST /admin/posts/{id}/unpublish` and `DELETE /admin/posts/{id}?reason=`. For a merchant's post a reason is required (422 without) and the merchant is notified with it. A deleted post stays in the database, hidden everywhere, and its history is kept.

Every change is written to the post's history (section 6).

## 2. Public endpoints

### GET /cms/blogs
Live posts, pinned first, then newest first.

Query: `kind` (`blog` | `news`), `search` (title, summary, body, author, business), `category_id`, `category` (name), `tag`, `source` (`platform` | `merchant`), `tenant_id`, `enterprise_id`, `page`, `page_size` (max 100).

```json
{
  "items": [
    {
      "id": "0b6e…", "title": "Five calm habits", "slug": "five-calm-habits", "kind": "blog",
      "excerpt": "Small habits.", "cover_image_url": "/api/v1/uploads/blogs/c.jpg",
      "author_name": "Asha Merchant", "author_type": "merchant",
      "business_name": "Calm Clinic", "business_logo_url": "https://cdn.example/calm.png",
      "tenant_id": "…", "enterprise_id": "…", "category": "Health", "category_id": "…",
      "tags": ["wellness"], "is_pinned": false, "status": "published",
      "published_at": "2026-10-21T09:00:00", "view_count": 12,
      "created_at": "2026-10-20T08:00:00", "updated_at": "2026-10-21T09:00:00"
    }
  ],
  "pagination": { "total": 1, "page": 1, "page_size": 20, "total_pages": 1 }
}
```

For a merchant post show `business_name` and `business_logo_url` as the author. For a platform post (`author_type: "platform"`) show the platform name.

### GET /cms/blogs/categories
Active categories in their set order, with the number of live posts. `?kind=news` counts only that kind. Use it for the filter.

```json
{ "items": [ { "id": "…", "name": "Health", "is_active": true, "sort_order": 0, "post_count": 4 } ], "total": 1 }
```

### GET /cms/blogs/{slug}
One live post (the id also works). Same fields as the list plus `content`. It counts one view. A post that is not live is `404 {"detail": "Blog post not found"}`.

## 3. Merchant endpoints (`/my/posts`)

All are limited to the caller's business: another business's post is a plain 404. Colleagues at the same business see and edit the same posts.

| Method and path | What it does |
|---|---|
| `GET /my/posts?status=&kind=&category_id=&search=&page=&page_size=` | the business's posts, newest change first (deleted ones are not listed) |
| `POST /my/posts` | save a draft (201) |
| `GET /my/posts/{id}` | one post, with `reviewer_note`, `actions`, and `live_version` when an edit is waiting |
| `PATCH /my/posts/{id}` | edit (see 1.2 for live posts) |
| `POST /my/posts/{id}/submit` | send for approval |
| `POST /my/posts/{id}/unpublish` | take a live post down (body `{"reason": "..."}` optional) |
| `POST /my/posts/{id}/discard-changes` | drop an edit that was rejected or sent back |
| `DELETE /my/posts/{id}` | delete a draft |
| `GET /my/posts/{id}/history` | who did what and when |

Create body:

```json
{
  "kind": "blog",
  "title": "Five calm habits",
  "excerpt": "Small habits.",
  "content": "<p>Start with your breathing…</p>",
  "category_id": "5f0c…",
  "cover_image_url": "/api/v1/uploads/blogs/c.jpg",
  "tags": ["wellness", "office"]
}
```

Only `title` is needed to save a draft. `enterprise_id` is for the rare business with more than one profile; it defaults to the first. The author is the logged-in user. The business name and logo are copied from the business profile. Upload the cover image first with `POST /api/v1/uploads/` and send the returned url.

Limits: title 150 characters, summary 300, body 100,000, up to 10 tags of 30 characters. `cover_image_url` must be an http(s) address or a path on this site. A category that is switched off cannot be picked (422).

A merchant post as the merchant sees it:

```json
{
  "id": "0b6e…", "title": "Five calm habits", "slug": "five-calm-habits", "kind": "blog",
  "status": "changes_requested", "status_label": "Changes Requested", "is_live": false, "has_pending_revision": false,
  "reviewer_note": "Please add a source for the claim in paragraph two.", "reviewed_at": "2026-10-21T10:00:00",
  "submitted_at": "2026-10-21T09:00:00", "actions": ["edit", "submit", "delete"],
  "content": "<p>…</p>", "live_version": null
}
```

`actions` lists what the caller can do with the post right now, so the app can show or hide buttons: merchant `edit`, `submit`, `discard_changes`, `unpublish`, `delete`; Super Admin `edit`, `publish`, `review`, `unpublish`, `delete`, `pin` / `unpin`.

## 4. Super Admin endpoints (`/admin`)

| Method and path | What it does |
|---|---|
| `GET /admin/queue?kind=&category_id=&enterprise_id=&tenant_id=&search=` | posts pending approval, **oldest first** (`enterprise_id` is the business) |
| `GET /admin/posts?status=&source=&kind=&category_id=&category=&tag=&search=&tenant_id=&enterprise_id=&pinned=` | all posts. Deleted ones only with `status=deleted` |
| `POST /admin/posts` | create a platform post. `status`: `draft` or `published` |
| `GET /admin/posts/{id}` | one post |
| `PUT /admin/posts/{id}` | edit; applies at once. 409 while a merchant edit is waiting |
| `POST /admin/posts/{id}/review` | `{"action": "approve" \| "request_changes" \| "reject", "reason": "..."}` |
| `POST /admin/posts/{id}/publish` | publish a draft, unpublished, sent-back or rejected post |
| `POST /admin/posts/{id}/unpublish` | take a live post down. Body `{"reason": "..."}`, required for a merchant's post |
| `PUT /admin/posts/{id}/pin` | `{"pinned": true}`. A pinned post stays at the top of its list |
| `DELETE /admin/posts/{id}?reason=` | delete. Reason required for a merchant's post |
| `PATCH /admin/posts/{id}/status` | older single endpoint: `published`, `unpublished` (`archived` too), `draft` |
| `GET /admin/posts/{id}/history` | who did what and when |

Review rules: the post must be `pending_approval` (409 otherwise). `request_changes` and `reject` need a reason of 3 to 500 characters (422). Approving also applies a merchant's waiting edit. A Super Admin cannot review a post they wrote.

`GET /admin/queue` item (same shape as in `/my/posts`):

```json
{
  "id": "0b6e…", "title": "Five calm habits", "kind": "blog", "status": "pending_approval", "status_label": "Pending Approval",
  "author_type": "merchant", "author_name": "Asha Merchant", "business_name": "Calm Clinic", "enterprise_id": "…",
  "category": "Health", "is_live": false, "has_pending_revision": false, "submitted_at": "2026-10-21T09:00:00",
  "actions": ["edit", "review", "delete", "pin"]
}
```

## 5. Categories (Super Admin)

| Method and path | What it does |
|---|---|
| `GET /admin/categories` | all categories, including switched-off ones, with the number of posts |
| `POST /admin/categories` | `{"name": "Health", "is_active": true}`. A name that exists in any letter case is 409 |
| `PATCH /admin/categories/{id}` | rename and/or `{"is_active": false}`. Renaming updates the name on the category's posts |
| `PUT /admin/categories/order` | `{"ids": [...]}`: every category id once, in the new order |
| `DELETE /admin/categories/{id}` | only a category with no posts. Otherwise 409: switch it off instead |

A switched-off category disappears from the public filter and cannot be chosen for a post, but the posts that already have it keep it. A post has exactly one category and an optional list of tags. The team should create the starter categories before launch (for example Health, Wellness, Events, Offers, Announcements, Tips).

## 6. History

`GET /my/posts/{id}/history` and `GET /admin/posts/{id}/history`, newest first. Rows are never changed or removed.

```json
{
  "items": [
    { "id": "…", "action": "changes_requested", "from_status": "pending_approval", "to_status": "changes_requested",
      "reason": "Add a source please", "actor_id": "…", "actor_name": "Platform Root", "actor_role": "super_admin",
      "created_at": "2026-10-21T10:00:00" }
  ],
  "total": 1
}
```

`action` is one of: `created`, `edited`, `submitted`, `approved`, `changes_requested`, `rejected`, `published`, `unpublished`, `deleted`, `pinned`, `unpinned`.

## 7. Notifications

They use the existing notification feed (and push to the app). They are sent after the change is saved and never make the API call fail.

| Category | To | When |
|---|---|---|
| `blog_post_submitted` | all Super Admins | a merchant sent a post or an edit of a live post (`metadata.is_edit`) |
| `blog_post_approved` | the post's author | approved by review, or published by a Super Admin |
| `blog_post_changes_requested` | the author | `metadata.reason` |
| `blog_post_rejected` | the author | `metadata.reason` |
| `blog_post_unpublished` | the author | a Super Admin took a live merchant post down, `metadata.reason` |
| `blog_post_deleted` | the author | a Super Admin deleted it, `metadata.reason` |

Metadata always has `post_id`, `entity_type: "blog_post"`, `entity_id`, `kind`, `title`, `slug`, `status` and `business_name`. The reason is in the metadata for the app to show, and is left out of the push payload and the message text. Only the author is told about a decision. Other admins of the same business are not.

## 8. Errors

| Code | When |
|---|---|
| 401 | no token on a protected route |
| 403 | wrong role (for example an Enterprise Admin on `/admin`, a customer on `/my`), or reviewing your own post |
| 404 | no such post, a deleted post, another business's post, or a post that is not live on the public routes |
| 409 | the post is in the wrong state (edit while pending, review a draft, delete a live post), a duplicate category name, deleting a category that has posts, editing while a merchant edit is waiting |
| 422 | missing or invalid fields: `Add the category, body before you send this for approval`, a reason missing or too short, a category switched off, a limit exceeded |

## 9. What changed for existing clients

- The admin routes were open to Enterprise Admins as well. They are now Super Admin only; businesses use `/my/posts`.
- Status `archived` is now `unpublished` (the old endpoint still accepts `archived`). Posts that were soft-deleted now have status `deleted`.
- The SEO fields (`seo_title`, `seo_description`) are no longer read or returned. SEO tools are out of scope.
- New fields on posts: `kind`, `author_type`, `business_name`, `business_logo_url`, `category_id`, `is_pinned`. Admin and merchant views also have `status_label`, `is_live`, `has_pending_revision`, `reviewer_note`, `reviewed_at`, `submitted_at`, `actions`.
- `DELETE /admin/posts/{id}` now says "Blog post deleted".
- Categories were free text before. They are now real categories; the migration creates one for each distinct name already on a post.

## 10. Choices made where the feature document left a decision open

These are in place now and are easy to change:

1. Editing a published merchant post keeps the live version up until the edit is approved.
2. Tags are included (optional, up to 10).
3. Blog and News are two kinds of post, with their own lists.
4. Merchants can unpublish their own live posts but delete drafts only.
5. A plain view count is returned on each post, with no dashboard.
6. Only the business's Enterprise Admin (owner / tenant admin) can write posts. Providers (internal users) cannot.
7. An unpublished merchant post that is sent again goes through approval again. Only a Super Admin can publish without it.
