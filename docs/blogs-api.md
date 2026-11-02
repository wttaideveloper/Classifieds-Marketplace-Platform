# Blogs and News API

For the web and mobile developers. Base path: `/api/v1/cms/blogs`. All times are UTC.

Three kinds of caller:

- **Public / any customer**: reads live posts. No login needed.
- **Merchant**: the business's Enterprise Admin (`tenant_owner` / `tenant_admin`). Writes posts for their own business under `/my/posts`. Every post needs a Super Admin's approval before the public sees it.
- **Super Admin**: writes platform posts (live at once), decides merchant posts, manages categories, under `/admin/...`.

An Enterprise Admin cannot use the `/admin` routes. A provider or customer cannot use `/my` (403). Without a token the protected routes answer 401.

A **Super Admin** builds the **forms** that decide which fields a post has (section 9). A business is given one active form for Blogs and one for News; a post is written with a version of it.

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
- Super Admin: `POST /admin/posts/{id}/unpublish` and `DELETE /admin/posts/{id}?reason=`. Unpublishing a merchant's post needs a reason. **Deleting always needs a reason** (422 `REASON_REQUIRED`), for platform posts too; it is kept in the history. For a merchant's post the merchant is notified with it. A deleted post stays in the database, hidden everywhere, and its history is kept.

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
One live post (the id also works). Same fields as the list plus `content`, `additional_images`, `reference_urls`, `supporting_documents` and `custom_fields` (section 9.4). It counts one view. A post that is not live is `404 {"detail": "Blog post not found", "code": "POST_NOT_FOUND"}`.

It never returns reviewer notes, moderation fields, a waiting edit (`pending_revision`), the form, the version, or any field the form marks internal. The public always reads the live columns of the post.

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
| `GET /my/form?kind=blog` | the form to write this kind of post with (section 9.3) |
| `POST /my/posts/preview`, `GET /my/posts/{id}/preview` | preview an unsaved or a saved post (section 12) |
| `GET /my/posts/{id}/revisions`, `.../revisions/{history_id}` | copies of the content after each change (section 12) |
| `POST /uploads`, `GET /uploads/limits` | files for the post (section 10) |

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

The detail also has `version` (and an `ETag` header), `form_id`, `form_version`, `form` (the fields the post is written with), `custom_fields` (values by key), `additional_images`, `reference_urls`, `supporting_documents` and, while an edit of a live post waits or was sent back, `pending_revision: {submitted_at, changed_fields, status}` next to `live_version`.

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
| `PUT /admin/posts/{id}/pin` | `{"pinned": true}`. A pinned post stays at the top of its list. Only a live post can be pinned (409 `NOT_LIVE`) |
| `DELETE /admin/posts/{id}?reason=` | soft delete. A reason is always required |
| `PATCH /admin/posts/{id}/status` | older single endpoint: `published`, `unpublished` (`archived` too), `draft` |
| `GET /admin/posts/{id}/history` | who did what and when |
| `POST /admin/posts/preview`, `GET /admin/posts/{id}/preview` | preview an unsaved or a saved post |
| `GET /admin/posts/{id}/revisions`, `.../revisions/{history_id}` | copies of the content after each change |
| `/admin/forms...` | the form builder (section 9) |

Review rules: the post must be `pending_approval` (409 `NOT_PENDING_APPROVAL` otherwise). `request_changes` and `reject` need a reason of 3 to 500 characters (422 `REASON_REQUIRED`). Approving also applies a merchant's waiting edit. A Super Admin cannot review a post they wrote.

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
      "post_version": 5, "created_at": "2026-10-21T10:00:00" }
  ],
  "total": 1
}
```

`action` is one of: `created`, `edited`, `submitted`, `approved`, `changes_requested`, `rejected`, `published`, `unpublished`, `deleted`, `pinned`, `unpinned`. `post_version` is the post's `version` after the change.

## 7. Notifications

They use the existing notification feed (and push to the app). They are sent after the change is saved and never make the API call fail.

| Category | To | When |
|---|---|---|
| `blog_post_submitted` | all Super Admins | a merchant sent a post or an edit of a live post (`metadata.is_edit`) |
| `blog_post_approved` | the post's author | approved by review |
| `blog_post_published` | the author | a Super Admin published the merchant's post directly (`/publish`), with no review step |
| `blog_post_changes_requested` | the author | `metadata.reason` |
| `blog_post_rejected` | the author | `metadata.reason` |
| `blog_post_unpublished` | the author | a Super Admin took a live merchant post down, `metadata.reason` |
| `blog_post_deleted` | the author | a Super Admin deleted it, `metadata.reason` |

Metadata on every one: `post_id`, `entity_type: "blog_post"`, `entity_id`, `kind` and `post_type` (blog or news), `title`, `slug`, `status`, `enterprise_id`, `business_name`, `tenant_id`, `author_id`, `author_name`, `occurred_at` (UTC, ISO 8601 with `Z`) and `deep_link`; plus `reason` when there is one. `deep_link` is the page that opens the post **for that reader**: the Super Admin's page for `blog_post_submitted`, the Enterprise Admin's page for the others. The two address templates are set on the server (`BLOG_DEEP_LINK_SUPER_ADMIN`, default `/admin/cms/posts/{post_id}`, and `BLOG_DEEP_LINK_ENTERPRISE`, default `/enterprise/cms/posts/{post_id}`); **the web team must confirm the real routes**, they are guesses until then. The reason is in the metadata for the app to show, and is left out of the push payload and the message text. Only the author is told about a decision. Other admins of the same business are not. A merchant's own actions (submit, unpublish, delete a draft) notify the Super Admins only for a submission.

## 8. Errors

Every error from these routes has the same body. `detail` is a plain sentence you can show; `code` is for the program to branch on; some add keys of their own.

```json
{ "detail": "Another post of the same kind already has this title. Use a different title", "code": "DUPLICATE_TITLE", "field": "title" }
```

A validation failure also has `errors`, one entry per problem, so a form can mark every field at once. With one problem `detail` is that problem's sentence; with several it is "Some fields need attention".

```json
{ "detail": "Add the event date before you send this for approval", "code": "VALIDATION_FAILED",
  "errors": [ { "field": "custom_fields.event_date", "code": "REQUIRED", "message": "Event date is required" } ] }
```

`errors[].field` is the field key (`title`, `content`, `category`, `cover_image`, `custom_fields.<key>`, `definition.sections[0].fields[2]` for a form). **One exception:** a request the framework itself cannot read (broken JSON, a wrong type such as a number where text is expected, a missing required key such as `title`) still answers `422 {"detail": [ {"loc": [...], "msg": "...", "type": "..."} ]}`, as every other endpoint of this API does.

| Status | `code` | When |
|---|---|---|
| 400 | `INVALID_IF_MATCH` | the `If-Match` header is not a version number |
| 401 | `UNAUTHENTICATED` | no token on a protected route |
| 403 | `FORBIDDEN` | wrong role (an Enterprise Admin on `/admin`, a customer on `/my`), or a business that is not yours |
| 403 | `SELF_REVIEW` | reviewing your own post |
| 404 | `POST_NOT_FOUND` | no such post, a deleted post, another business's post, or a post that is not live on the public routes |
| 404 | `FORM_NOT_FOUND`, `FORM_VERSION_NOT_FOUND`, `REVISION_NOT_FOUND`, `CATEGORY_NOT_FOUND`, `FILE_NOT_FOUND` | no such form, version, revision, category or file (a file you may not read is also `FILE_NOT_FOUND`) |
| 409 | `VERSION_CONFLICT` | the post (or form draft) changed after you loaded it. Has `current_version` |
| 409 | `DUPLICATE_TITLE` | another post of the same kind in the same business (or among platform posts) has this title, ignoring letter case. Has `field: "title"` |
| 409 | `DUPLICATE_SLUG` | a Super Admin asked for a slug that is taken (deleted posts keep theirs). Has `field: "slug"` |
| 409 | `DUPLICATE_CATEGORY`, `DUPLICATE_FORM_NAME` | the name exists in any letter case |
| 409 | `INVALID_STATUS` | the post's status does not allow this (for example publishing a deleted post, submitting a published one) |
| 409 | `IN_REVIEW` | editing or publishing a post that is pending approval |
| 409 | `PENDING_REVISION` | a Super Admin editing while a merchant's edit waits |
| 409 | `NOT_PENDING_APPROVAL`, `NOT_LIVE`, `ONLY_DRAFT_DELETABLE`, `NOTHING_TO_DISCARD` | the action does not fit the post's state |
| 409 | `CATEGORY_IN_USE`, `FORM_IN_USE` (has `post_count`), `FORM_NOT_PUBLISHED` | delete a used category or form; activate a form never published |
| 413 | `FILE_TOO_LARGE` | an upload over the limit. Has `max_mb` |
| 415 | `UNSUPPORTED_TYPE` | a file that is not an accepted image or document, or whose content does not match its name |
| 422 | `VALIDATION_FAILED` | missing or invalid fields. Has `errors` |
| 422 | `REASON_REQUIRED` | a reason of 3 to 500 characters is needed |
| 422 | `INVALID_FORM`, `STANDARD_FORM_FIXED` | the form definition has problems (`errors` lists each), or a Standard form was changed |
| 422 | `CATEGORY_NOT_FOUND`, `CATEGORY_INACTIVE`, `NO_BUSINESS`, `INVALID_SLUG`, `RESERVED_SLUG`, `INVALID_ORDER`, `INVALID_STATUS_FILTER` | the single-reason refusals |
| 422 | `EMPTY_FILE`, `INVALID_FILE`, `VIRUS_DETECTED` | upload content problems |
| 503 | `SCANNER_UNAVAILABLE` | the virus scanner is required but down |

Codes inside `errors[]`: `REQUIRED`, `TOO_LONG`, `TOO_SHORT`, `TOO_MANY`, `TOO_SMALL`, `TOO_LARGE`, `INVALID_TYPE`, `INVALID_FORMAT`, `INVALID_OPTION`, `UNKNOWN_FIELD` (a custom field the form does not have for this author), `NOT_IN_FORM` (a built-in field the form switched off), `FILE_NOT_FOUND`, `WRONG_FILE_KIND`.

## 9. Forms (Super Admin builds, Enterprise Admin writes)

A **form** says which fields a post has and how each behaves. A form has a **type** (`blog`, `news` or `both`), a **mode** (`standard`: the built-in fields, which cannot be changed; `configurable`: you set them) and a **scope** (`global`: every business; `selected`: only the businesses chosen).

### 9.1 Life of a form

1. `POST /admin/forms` saves a **draft**. Nothing uses it yet.
2. `PATCH /admin/forms/{id}` edits the draft (name, description, type, mode, scope, `tenant_ids`, `definition`). Send `base_revision` (the `revision` you loaded) so you do not save over someone else's work (409 `VERSION_CONFLICT`).
3. `POST /admin/forms/{id}/publish` freezes the draft as the next **version** (1, 2, 3...). A published version never changes. The first publish also makes the form **active**.
4. `POST /admin/forms/{id}/deactivate` / `activate` switches whether new posts get it. Activating a form that was never published is 409 `FORM_NOT_PUBLISHED`.
5. `DELETE /admin/forms/{id}` works only while no post was written with it (409 `FORM_IN_USE`, `post_count`). Deactivate it instead.

| Method and path | What it does |
|---|---|
| `GET /admin/forms?form_type=&status=&is_active=&scope=&tenant_id=&search=&page=&page_size=` | forms, newest edit first. `status` is `draft` (never published) or `published` |
| `GET /admin/forms/{id}` | the form with its draft `definition`, `assignments` (tenant and business name), `revision`, `current_version`, `has_unpublished_changes`, `post_count` |
| `PUT /admin/forms/{id}/assignments` | `{"scope": "selected", "tenant_ids": [...]}` or `{"scope": "global"}`. Takes effect at once |
| `GET /admin/forms/{id}/versions`, `GET /admin/forms/{id}/versions/{n}` | the published versions, and exactly one version with its fields |
| `GET /admin/forms/{id}/preview`, `POST /admin/forms/preview` | the fields as the writer would see them, for the saved draft or for an unsaved `definition`. Nothing is stored |
| `GET /admin/forms/active?tenant_id=` | the blog form and the news form a business gets right now. Without `tenant_id`: what platform posts use (global forms only) |

### 9.2 The definition

```json
{
  "sections": [
    { "key": "basics", "title": "Basics", "description": null, "fields": [
        { "key": "title", "help_text": "Keep it short", "validation": { "max_length": 80 } },
        { "key": "category" },
        { "key": "excerpt", "required": true } ] },
    { "key": "details", "title": "Event details", "fields": [
        { "key": "event_date", "type": "date", "label": "Event date", "required": true },
        { "key": "audience", "type": "select", "label": "Audience", "options": [ { "value": "kids", "label": "Kids" }, { "value": "adults", "label": "Adults" } ] },
        { "key": "editor_note", "type": "textarea", "label": "Note for editors", "visibility": "internal" },
        { "key": "partner", "type": "text", "label": "Partner", "enterprise_only": true } ] }
  ]
}
```

The order of sections and of fields in the lists is their order. What comes back is the cleaned version, every field with all its settings: `key`, `kind` (`core` | `custom`), `type`, `label`, `required`, `visible`, `visibility` (`public` | `internal`), `enterprise_only`, `help_text`, `placeholder`, `options`, `validation`.

- **Built-in (core) fields**, by key: `title`, `excerpt`, `content` (the body), `category`, `tags`, `cover_image`, `additional_images`, `reference_urls`, `supporting_documents`. Their type is fixed. You can set `label`, `required`, `visible`, `help_text`, `placeholder` and limits: `title` (`min_length`, `max_length` up to 150), `excerpt` (up to 300), `content` (up to 100,000 characters of text), `tags` (`max_items` up to 10), and `max_items` for the three lists (capped by the server limits). `title`, `content` and `category` must be in the form and are always shown, required and public. A built-in field left out of the form is simply not offered.
- **Custom fields**, any other key (2 to 40 lowercase letters, digits or underscores, starting with a letter): `type` is `text`, `textarea`, `number`, `date`, `select`, `multiselect`, `checkbox`, `url` or `email`. Rules in `validation`: text and textarea `min_length`, `max_length`, `pattern` (a regular expression); number `min`, `max`, `integer`; date `min`, `max` (YYYY-MM-DD); multiselect `max_items`. `select` and `multiselect` need `options` (up to 50).
- `visible: false` hides a field from the form. `required` only counts while it is visible. `enterprise_only: true` means only an Enterprise Admin's posts have the field, not platform posts. `visibility: "internal"` keeps a custom field, `additional_images`, `reference_urls` or `supporting_documents` off the public page (admins still see it).
- A **Standard** form is always the built-in layout: `definition` is ignored when you create it, and a different one is 422 `STANDARD_FORM_FIXED`. Up to 20 sections and 60 fields.

A bad definition is 422 `INVALID_FORM` with an `errors` entry for each problem (`field` is a path such as `definition.sections[1].fields[0].options`).

### 9.3 Which form a post gets

For a new post the server picks, among forms that are **active, published** and of the post's type (or `both`): one assigned to the business (`selected`) before a `global` one; if still tied, the one published most recently. Platform posts use global forms only. With none, the built-in Standard form applies: `form_id: null`, `version: 0`.

- `GET /my/form?kind=blog|news` gives the Enterprise Admin the form, with sections and fields, to build the editor from.
- The post stores `form_id` and `form_version` when it is created or saved, and answers with them and with `form` (the exact fields of that version).
- **A live post keeps its version for good**, and so does a post waiting for approval, so publishing a new version never changes a live post or blocks an approval over a rule the author never saw. An edit of a live post is checked against the same version. A post that is not live (draft, changes requested, rejected, unpublished) follows the active form: `form` shows it, and the post is moved to it when it is next saved or submitted.
- Old versions stay readable (`GET /admin/forms/{id}/versions/{n}`), and the post's own `form` shows its version.

### 9.4 Custom values

Send them in `custom_fields` as an object of key to value on create and edit. Only the keys sent are changed; `null` or an empty value clears one. They are checked against the post's form: a key the form does not have for this author is 422 `UNKNOWN_FIELD`; a wrong type, option, length or range gets its own entry in `errors`. A draft may leave required fields empty; **submit and publish refuse** until they are filled (`Add the event date before you send this for approval`). On the public page `custom_fields` is a list in form order of only the public fields, with their labels:

```json
"custom_fields": [ { "key": "event_date", "label": "Event date", "type": "date", "value": "2026-12-01" } ]
```

Core fields work the same: a field the form made `required` is needed to submit, and a limit set on it (`max_length: 80` on the title) is checked on every save.

## 10. Uploads

`POST /uploads` (Super Admin or Enterprise Admin), `multipart/form-data`:

| Part | |
|---|---|
| `file` | the file |
| `purpose` | `cover_image` or `image` (a picture), or `document` |

| | Images | Documents |
|---|---|---|
| Types | JPEG, PNG, WebP, GIF (read from the content; SVG is not accepted) | PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, TXT, CSV (read from the content; must agree with the extension) |
| Size | 5 MB (`BLOG_MAX_IMAGE_MB`), and the picture's size limits | 10 MB (`BLOG_MAX_DOCUMENT_MB`) |
| Counts | 1 cover, up to 10 `additional_images` | up to 10 `supporting_documents`, up to 10 `reference_urls` |

`GET /uploads/limits` returns all of these, so the form can check before it uploads. Every file also goes through the virus scan when the server has one. The answer (201):

```json
{ "file_id": "…", "url": "https://api.example.com/api/v1/cms/blogs/files/9d1c…", "name": "cover.png", "size": 48210,
  "content_type": "image/png", "purpose": "cover_image", "width": 1200, "height": 630, "scan_status": "clean", "created_at": "2026-10-21T09:00:00" }
```

Put `url` in `cover_image_url`, `additional_images[].url` or `supporting_documents[].url` (`name`, `size` and `content_type` of a document are filled in for you). A cover or extra image must be an image upload and a document a document upload (422 `WRONG_FILE_KIND`); a file uploaded by another business is 422 `FILE_NOT_FOUND`.

`GET /files/{file_id}`: images show inline, documents download. A file is **public only while a live post uses it** (cover, extra images, documents or an image in the body). Before that, and after the post is taken down, only the business that uploaded it and the Super Admins can fetch it (send the token); anyone else gets 404 `FILE_NOT_FOUND`. Responses carry `X-Content-Type-Options: nosniff`. Uploaded files are kept after a post is deleted; there is no clean-up job yet.

Upload errors: 413 `FILE_TOO_LARGE` (`max_mb`), 415 `UNSUPPORTED_TYPE`, 422 `EMPTY_FILE`, `INVALID_FILE` (not decodable, too many pixels), `VIRUS_DETECTED`, 503 `SCANNER_UNAVAILABLE`, 401 / 403 for the wrong caller.

## 11. Changing a post at the same time as someone else

Every post has a `version`, starting at 1, that goes up with each change (edit, submit, decision, publish, unpublish, pin, delete). It is in every post answer and in the `ETag` header (`"3"`). To refuse a change made on an old copy, send the version you loaded as `base_version` in the body of an edit, or as `If-Match: "3"` on any change (edit, submit, review, publish, unpublish, pin, delete, discard). If the post has changed since:

```json
409 { "detail": "This post was changed after you opened it. Reload it, then apply your changes again", "code": "VERSION_CONFLICT", "current_version": 5 }
```

and nothing is changed. Sending no version means "overwrite", so a client that does not care keeps working. A review made with the version the Super Admin looked at cannot approve a post the merchant edited meanwhile. Forms use `revision` and `base_revision` the same way.

Two people changing a post at the very same moment are also serialised by a database row lock (effective on PostgreSQL; the tests run on SQLite, where it does nothing), so one of them waits for the other.

## 12. Previews and revisions

- `POST /my/posts/preview` and `POST /admin/posts/preview` take the same body as a create (all fields optional) and return the post as a reader would see it, **without saving**. `GET /my/posts/{id}/preview` and `GET /admin/posts/{id}/preview` do the same for the saved post, including a merchant's waiting edit.
- The answer is public-shaped (`preview: true`, content cleaned the same way as when saved, only public custom fields with labels, `form`) plus `ready_to_submit` and `problems` (`[ {field, code, message} ]`): what is still missing for the form, so the editor can show a checklist.
- `GET .../revisions` lists the changes that kept a copy of the content (`created`, `edited`, `submitted`, `approved`, `published`), newest first, with `post_version`, `title` and `form_version`. `GET .../revisions/{history_id}` returns that copy as `snapshot` (the post's own fields, `custom_fields` and the form). Revisions are read-only: there is no restore.

## 13. Status transitions

| From | Action (who) | To | Notes |
|---|---|---|---|
| (new) | create (Enterprise Admin) | `draft` | |
| (new) | create with `status: "published"` (Super Admin) | `published` | platform posts only, no review |
| `draft`, `changes_requested`, `rejected`, `unpublished` | `PATCH` (Enterprise Admin) | same | content changes only |
| `draft`, `changes_requested`, `rejected`, `unpublished` | `submit` (Enterprise Admin) | `pending_approval` | all required fields of the form needed |
| `published` | `PATCH` (Enterprise Admin) | `pending_approval` | still live with the old version |
| `pending_approval` | `review: approve` (Super Admin) | `published` | applies a waiting edit; not your own post |
| `pending_approval` | `review: request_changes` / `reject` (Super Admin) | `changes_requested` / `rejected` | reason required; a live post stays live |
| `draft`, `changes_requested`, `rejected`, `unpublished` | `publish` (Super Admin) | `published` | author told with `blog_post_published` |
| `published` | `unpublish` (Enterprise Admin or Super Admin) | `unpublished` | reason required for a Super Admin on a merchant's post; a waiting edit becomes the post's content |
| `changes_requested`, `rejected` (of a live post) | `discard-changes` (Enterprise Admin) | `published` | drops the sent-back edit |
| `draft` | `DELETE` (Enterprise Admin) | `deleted` | drafts only |
| any but `deleted` | `DELETE` (Super Admin) | `deleted` | reason required; soft delete |
| any | `pin` / `unpin` (Super Admin) | unchanged | live posts only; unpublishing unpins |

Anything else is 409 (`INVALID_STATUS`, `IN_REVIEW`, `NOT_PENDING_APPROVAL`, `NOT_LIVE`, ...). `deleted` is final.

## 14. Who can do what

| | Public / customer | Provider | Enterprise Admin | Super Admin |
|---|---|---|---|---|
| Read live posts, categories, public files | yes | yes | yes | yes |
| Own posts: create, edit, submit, unpublish, delete draft, preview, history, revisions | no (401/403) | no (403) | their business only | no (`/my` is for businesses) |
| `GET /my/form` | no | no | yes | no |
| Upload files | no | no | yes | yes |
| See all posts, the queue, details, history, revisions | no | no | no | yes |
| Approve, request changes, reject | no | no | no | yes (not own posts) |
| Create and edit platform posts, publish, unpublish, pin, delete | no | no | no | yes |
| Categories: create, rename, reorder, switch on/off, delete unused | no | no | no | yes |
| Forms: create, edit, publish, assign, activate, delete | no | no | no | yes |
| Categories for choosing (active only) | yes (`GET /categories`) | yes | yes | all, via `/admin/categories` |

## 15. Lists and pages

Every list uses `page` (from 1) and `page_size` (default 20, at most 100) and answers `{ "items": [...], "pagination": { "total", "page", "page_size", "total_pages" } }`. This applies to the public list, `/my/posts`, `/admin/posts`, `/admin/queue` and `/admin/forms`. The category, version, history and revision lists are not paged: they answer `{ "items": [...], "total" }`. There is no cursor paging in this module.

## 16. Settings the team can change

| Setting | Default | |
|---|---|---|
| `BLOG_MAX_IMAGE_MB`, `BLOG_MAX_DOCUMENT_MB` | 5, 10 | upload size limits |
| `BLOG_MAX_ADDITIONAL_IMAGES`, `BLOG_MAX_DOCUMENTS`, `BLOG_MAX_REFERENCE_URLS` | 10, 10, 10 | counts per post |
| `BLOG_REQUIRE_UNIQUE_TITLES` | on | switch off to allow two posts with one title |
| `BLOG_DEEP_LINK_SUPER_ADMIN`, `BLOG_DEEP_LINK_ENTERPRISE` | see section 7 | where a notification opens the post |

## 17. What changed for existing clients

- `actions` on a post is unchanged. Deleting as a Super Admin now needs a reason for every post (it was only for a merchant's post), and a post can be pinned only while live.
- A direct publish of a merchant's post now sends `blog_post_published` (it sent `blog_post_approved`).
- Errors from these routes now have a `code` next to `detail`. `detail` is still the same sentence, so a client that only shows `detail` keeps working.
- Two posts of one kind in one business can no longer share a title (409 `DUPLICATE_TITLE`).
- A slug sent by a Super Admin that is already used is now 409 `DUPLICATE_SLUG` (it used to be silently changed). Slugs made from titles are still made unique with a number.
- New post fields: `additional_images`, `reference_urls`, `supporting_documents`, `custom_fields`, `version`, `form_id`, `form_version`. Posts that existed before use the built-in Standard form.

## 18. What changed earlier for existing clients

- The admin routes were open to Enterprise Admins as well. They are now Super Admin only; businesses use `/my/posts`.
- Status `archived` is now `unpublished` (the old endpoint still accepts `archived`). Posts that were soft-deleted now have status `deleted`.
- The SEO fields (`seo_title`, `seo_description`) are no longer read or returned. SEO tools are out of scope.
- New fields on posts: `kind`, `author_type`, `business_name`, `business_logo_url`, `category_id`, `is_pinned`. Admin and merchant views also have `status_label`, `is_live`, `has_pending_revision`, `reviewer_note`, `reviewed_at`, `submitted_at`, `actions`.
- `DELETE /admin/posts/{id}` now says "Blog post deleted".
- Categories were free text before. They are now real categories; the migration creates one for each distinct name already on a post.

## 19. Choices made where the feature document left a decision open

These are in place now and are easy to change:

1. Editing a published merchant post keeps the live version up until the edit is approved.
2. Tags are included (optional, up to 10).
3. Blog and News are two kinds of post, with their own lists.
4. Merchants can unpublish their own live posts but delete drafts only.
5. A plain view count is returned on each post, with no dashboard.
6. Only the business's Enterprise Admin (owner / tenant admin) can write posts. Providers (internal users) cannot.
7. An unpublished merchant post that is sent again goes through approval again. Only a Super Admin can publish without it.

## 20. Governance: Blogs & News per module (Super Admin)

Platform-wide switches, one per module: `events`, `trainings`, `products`, `services`, `general`. A module with nothing stored is on. Web cache key: `["cms", "governance"]`.

| Method and path | Who | What |
|---|---|---|
| `GET /governance` | anyone | the five switches, for the web app to decide which tabs, public feed filters and promo blocks to show |
| `GET /admin/governance` | Super Admin | same, with `updated_at` / `updated_by` |
| `PATCH /admin/governance/{module}` | Super Admin | body `{"enabled": false}`; answers the full configuration |
| `POST /admin/governance/reset` | Super Admin | all five on; answers the full configuration |
| `GET /admin/governance/audit?module=&page=&page_size=` | Super Admin | newest first, `{items, pagination}` |

```json
{ "modules": { "events": false, "trainings": true, "products": true, "services": true, "general": true },
  "updated_at": "2026-11-02T10:30:00", "updated_by": { "id": "user-id", "name": "Super Admin" } }
```
Audit row: `{ "id", "module": "events", "previous_value": true, "new_value": false, "action": "disable", "actor_id", "actor_name", "actor_role": "super_admin", "created_at" }` (`action`: `enable`, `disable`, `reset`). Times are UTC without a `Z`.

Behaviour: a post now has a `module` (default `general`, set on create, not changeable). While a module is off, creating a post for it is 409 `MODULE_DISABLED` (`module` in the body), for Enterprise Admins and Super Admins. Nothing else changes: existing posts, drafts, categories, forms and revisions stay, published posts stay in the lists and open by their URL, and drafts can still be edited and sent. `GET /cms/blogs?module=events` filters the public list. Hiding tabs, filters and promo blocks is for the web app, using `GET /governance`. Settings are platform-wide, not per business. Changes send no notifications.

Errors: 401 `UNAUTHENTICATED`; 403 `FORBIDDEN` (Enterprise Admin, provider, customer); 422 `INVALID_MODULE` (with `allowed`) or a missing `enabled`; 500 `SAVE_FAILED` (nothing changed). Setting the value a module already has answers 200 and writes no audit row. Concurrent updates take a row lock (effective on PostgreSQL only); the last write wins and each real change gets its own audit row.
