# eBook lessons (Trainings and Courses)

For the web (Enterprise Admin, Super Admin) and mobile (learner) developers. Everything below also works under `/courses/...` in place of `/trainings/...`. All times are UTC. Every endpoint needs a sign-in.

An eBook is a **lesson of type `ebook`**. Create the lesson with the usual lesson endpoints (`type: "ebook"`); its pages are then saved with the eBook endpoints below, not in the lesson's `content` field. Pages and pictures are stored as structured data, so page order, rich text, picture position, width, crop settings, alt text, captions, the completion rule and the bookmark / note switches all come back exactly as saved.

## 1. How it fits together

```
author edits DRAFT -> save -> validate -> send for approval
                                              |
                       Super Admin reviews: request changes -> author edits -> send again
                                              |
                                           approve -> PUBLISHED copy -> learners read it
```

- The author works on the **draft**. Learners only ever get the **published** copy, which is made when a Super Admin approves (or when the owner publishes an approved version).
- A save after approval puts the draft back to `draft`. What is live does not change until the new draft is approved and published.
- While an eBook is **waiting for approval** the draft is locked (409 `IN_REVIEW`). The author can withdraw it.

Statuses of the draft: `draft`, `pending_approval`, `changes_requested`, `approved`. `published_version` says which version is live (null = nothing live). `has_unpublished_changes` is true when the draft is not what is live.

## 2. The eBook structure

```json
{
  "ebook": {
    "subtitle": "An introduction to nutrition",
    "author": "Invigorate Health",
    "description": "Book description",
    "cover_asset_id": "5f0c0a52-6a0c-4f0e-9a52-0b7d0e2a9c11",
    "completion_rule": "read_to_end",
    "allow_bookmarks": true,
    "allow_notes": true,
    "pages": [
      {
        "id": "page_1", "order": 1, "title": "Introduction",
        "content_html": "<h2>Introduction</h2><p>Nutrition is...</p>",
        "images": [
          { "id": "image_block_1", "asset_id": "9a1c...", "alt_text": "Healthy food diagram", "caption": "Examples of healthy food",
            "width": "full", "fit": "cover", "aspect_ratio": "16:9", "position": "center",
            "decorative": false, "placement": "after_content" }
        ]
      }
    ]
  }
}
```

| Field | Rules |
|---|---|
| `completion_rule` | `read_to_end` (default), `opened`, `manual` |
| page `id` | yours (letters, digits, `-`, `_`, up to 64). Keep it the same between saves. Made up for you if left out |
| page `order` | 1, 2, 3 … The list order is used when `order` is left out. Stored renumbered without gaps |
| `title` | up to 200 characters. Required before the eBook can be sent for approval |
| `content_html` | rich text, see below. Up to 200 KB per page |
| image `asset_id` | from the image upload (section 6) |
| `width` | `small`, `medium`, `large`, `full` |
| `fit` | `cover`, `contain` |
| `aspect_ratio` | `original` or `w:h` such as `16:9`, `4:3`, `1:1` |
| `position` | which part stays in view when cropped: `center`, `top`, `bottom`, `left`, `right`, `top left`, `top right`, `bottom left`, `bottom right` |
| `alt_text` | up to 500 characters. Required before sending for approval, unless `decorative` is true |
| `decorative` | a picture that adds no meaning; alt text may then be empty |
| `placement` | `before_content`, `after_content` (default) or `inline`: the page's HTML then holds `<div data-image-id="image_block_1"></div>` where the picture goes |

Limits: 500 pages, 30 pictures per page, 200 KB of text per page, 5000 characters of description. A field that is not on this list is refused (422) so a typo such as `alt_txt` is never lost silently. The extra fields the server adds when it returns an eBook (`url`, `natural_width`, `natural_height`, `available` on pictures, `html_notes` on pages, `cover_url`) are accepted and ignored, so you can send back what you loaded.

**Please confirm the allowed values** for `width`, `position`, `placement` and `aspect_ratio` with the apps. If the editor uses others, tell us and we add them.

### Rich text

Kept: headings h1–h6, paragraphs, bold, italic, underline, strike, lists, quotes, code, links (`http`, `https`, `mailto`, `tel`, and `#anchors`), tables (with `colspan` / `rowspan`), sub / sup, highlight, small, `figure`, alignment / colour / weight / size as inline style, a few editor class names (such as `ql-align-center`), and the `data-image-id` placeholders. Removed: scripts, event handlers (`onclick` …), `javascript:` links, iframes, forms, CSS that can load or position things, and `<img>` (pictures are image blocks, never raw tags). What was removed comes back in `warnings` on the save, and is listed on the page (`html_notes`) so the editor can be fixed. The server cleans the text on every save; a browser must still render it as HTML and the apps should do the same.

## 3. Author endpoints (Enterprise Admin or provider of the training's business)

A Super Admin can read these but not change anything: they review.

`BASE = /api/v1/trainings/{trainingId}/sections/{sectionId}/lessons/{lessonId}/ebook`

| Method and path | What |
|---|---|
| `GET BASE` | the draft for editing, with each picture's `url`, `natural_width`, `natural_height` |
| `PUT BASE` | save the whole draft |
| `POST BASE/validate` | check it before sending |
| `PATCH BASE/pages/reorder` | `{"page_ids": ["page_3","page_1","page_2"]}` |
| `POST BASE/submit` | send for approval |
| `POST BASE/withdraw` | take it back from review |
| `POST BASE/publish` | make an approved eBook live |
| `POST BASE/unpublish` | take the live eBook down |
| `GET BASE/history` | every change and decision, and the versions kept |
| `GET BASE/versions/{n}?kind=` | one kept version |
| `POST BASE/versions/{n}/restore` | copy a kept version into the draft |

**GET** returns:

```json
{ "id": "…", "training_id": "…", "section_id": "…", "lesson_id": "…", "version": 4, "status": "draft", "status_label": "Draft",
  "published_version": 3, "published_at": "…", "has_unpublished_changes": true, "reviewer_note": null,
  "submitted_at": null, "reviewed_at": null, "updated_at": "…", "updated_by": { "id": "…", "name": "Asha Author" },
  "actions": ["save", "reorder", "validate", "submit", "unpublish", "history"],
  "imported_from_lesson_content": false, "ebook": { … } }
```

`actions` says what can be done now; use it to show or hide buttons. When nothing is saved yet you get an empty eBook with `version: 0` and `id: null`. If the lesson still has the older JSON in its `content` field you get that, cleaned, as the starting point (`imported_from_lesson_content: true`); save it once and it moves to the new storage.

**PUT** body: `{ "ebook": { … }, "base_version": 3 }`. Response:

```json
{ "id": "…", "lesson_id": "…", "version": 4, "status": "draft", "updated_at": "…", "published_version": 3, "has_unpublished_changes": true, "warnings": [] }
```

Send `base_version` (the version you loaded). If someone saved in between you get **409** and nothing is overwritten:

```json
{ "detail": { "code": "VERSION_CONFLICT", "message": "…", "current_version": 5, "your_version": 3, "updated_at": "…", "updated_by": "Dr Provider" } }
```

Load the newer version and apply your change on top. Without `base_version` the last save wins.

**Validate** returns the problems with where they are:

```json
{ "valid": false, "page_count": 3, "version": 4,
  "errors": [ { "page_id": "page_2", "image_id": "image_block_1", "field": "alt_text", "code": "alt_text_required", "message": "Image alt text is required" } ],
  "warnings": [] }
```

What it checks: at least one page; every page has a title; no empty pages (text or a picture); every picture has alt text (or is `decorative`); every picture exists, belongs to this business and its file is still there; no unsupported markup left in the text; the page order is 1…n; a valid completion rule; the cover picture. A picture set to appear `inline` without a placeholder in the text is a **warning**. `POST BASE/validate` with `{"ebook": {…}}` checks unsaved content.

**Submit** needs a valid draft (422 `EBOOK_INVALID` with the list of errors otherwise) and a change from what is live. Super Admins are notified.

**History** lists every save, reorder, submission, decision and publication with who and when, plus the versions kept (when sent, approved or published; ordinary saves are not kept as full copies).

## 4. Super Admin review

| Method and path | What |
|---|---|
| `GET /api/v1/admin/trainings/{trainingId}/sections/{sectionId}/lessons/{lessonId}/ebook?include_drafts=true` | the review view |
| `POST /api/v1/admin/trainings/{trainingId}/sections/{sectionId}/lessons/{lessonId}/ebook/review` | decide |
| `GET /api/v1/admin/ebooks/pending` | the eBooks waiting, oldest first |

The review view returns the **draft** and the **published** copy with their pages, formatted text, pictures, crop settings, alt text and captions, the `validation` result, `changes` (pages added, removed, changed, order changed, details changed), `last_editor`, `last_updated`, `submitted_by`, and the recent history. Without `include_drafts=true` only the published copy is returned.

Decide with `{"action": "approve", "publish": true}` or `{"action": "request_changes", "note": "…"}`. A note of at least 3 characters is required to ask for changes and the author reads it (`reviewer_note`, and in the notification). `approve` checks the eBook again, then (with `publish: true`, the default) makes it live for learners at once. With `publish: false` it is approved and the owner publishes later with `POST BASE/publish`. Only a Super Admin can decide; nothing unapproved can be published by anyone.

Notifications (existing feed and push): `ebook_submitted` to the Super Admins; `ebook_approved` and `ebook_changes_requested` (with `metadata.reason`) to the person who sent it.

## 5. Learner endpoints (mobile)

`L = /api/v1/trainings/{trainingId}/lessons/{lessonId}/ebook`

| Method and path | What |
|---|---|
| `GET L` | read the **published** eBook |
| `GET L/progress`, `PUT L/progress` | resume / save where I am |
| `POST L/progress/complete` | mark it read (only for `completion_rule: manual`) |
| `GET L/bookmarks`, `POST L/bookmarks`, `DELETE L/bookmarks/{id}` | bookmarks |
| `GET L/notes`, `POST L/notes`, `PATCH L/notes/{id}`, `DELETE L/notes/{id}` | notes |

**Access.** `GET L` gives only the published copy, after the course's own checks: an active enrolment (a pending or rejected enrolment gets the same coded 403 as `/content`), a training that is open, a lesson that is not in draft, and not locked by prerequisites or release rules (403 `LESSON_LOCKED`). 404 when nothing is published. The training's own staff and Super Admins can read the same published copy to preview it. Progress, bookmarks and notes are for enrolled learners only.

**Read response:** `{ id, training_id, lesson_id, title, version, published_at, page_count, ebook: { … } }`. Each picture has `url` (needs the same sign-in as everything else; send the bearer token when loading it), `natural_width`, `natural_height`. Editor-only fields (`html_notes`) are left out.

**Progress.**

```json
PUT L/progress   { "page_id": "page_3", "page_number": 3, "progress_percent": 42, "last_scroll_position": 860 }
→ { "last_page_id": "page_3", "last_page_number": 3, "last_scroll_position": 860, "progress_percent": 42, "completed": false,
    "completed_at": null, "page_count": 8, "version_read": 4, "lesson_completed": false }
```

`progress_percent` is the furthest the learner has read and never goes down; the last page and scroll position are always the latest. The page decides the page number (a stale `page_number` is ignored). An unknown `page_id` is 422. When the eBook is republished and the page the learner stopped on is gone, `GET L/progress` returns `last_page_id: null` (start from the first page) and keeps `progress_percent`.

**Completion** follows the eBook's `completion_rule`: `read_to_end` completes when the **last page** is saved with `progress_percent: 100`; `opened` completes on the first save; `manual` only with `POST L/progress/complete`. Completing counts the lesson as done in the course progress (the same as `POST /trainings/{id}/progress/complete-lesson`), and a locked lesson cannot be completed this way.

**Bookmarks** `{ "page_id": "page_3", "label": "Recipes", "scroll_position": 120 }`: 201 when new, 200 with the existing one for the same page and position. 403 when the eBook has `allow_bookmarks: false`. At most 200 per eBook. Listed in page order; `page_exists: false` when a later version removed the page (the bookmark is kept).

**Notes** `{ "page_id": "…", "text": "…", "quote": "the passage", "color": "yellow", "scroll_position": 300 }`: `color` is `yellow`, `green`, `blue`, `pink` or `orange`; text up to 5000 characters, plain text (markup is removed). 403 when `allow_notes` is false. At most 500 per eBook. Only the owner sees, changes or deletes a note (anyone else gets 404). `GET L/notes?page_id=` narrows to a page.

## 6. Pictures (the existing upload, plus asset APIs)

Upload with `POST /api/v1/trainings/upload` (form: `file`, `purpose=image`). For a picture the response now also carries what the editor needs:

```json
{ "url": "…/trainings/upload/…png", "name": "diagram.png", "size": 48210, "type": "image/png", "purpose": "image",
  "asset_id": "9a1c…", "asset_url": "…/api/v1/media/9a1c…/file", "width": 1600, "height": 900, "sha256": "…", "scan_status": "skipped" }
```

Use `asset_id` in an image block (and as `cover_asset_id`). The picture is stored on the upload volume (permanent as long as the volume is). `PUBLIC_MEDIA_BASE_URL` sets the address the links use, so a CDN or the https host can sit in front.

**Checked on upload:** the file really is a JPEG, PNG, WebP or GIF picture (it is decoded, not trusted from the name or the declared type), its name matches its type, the longest side is at most **8000 px** (`MAX_IMAGE_DIMENSION`), total pixels at most **40 million** (`MAX_IMAGE_PIXELS`), size at most **10 MB** (`MAX_IMAGE_SIZE_MB`), and the virus scan finds nothing. **SVG is not accepted**: an SVG file can carry scripts. Tell us if SVG is really needed and we will add it with a sanitizer.

**Virus scan:** set `CLAMAV_HOST` (and `CLAMAV_PORT`) to a ClamAV daemon. Without one nothing is scanned and `scan_status` is `skipped`. With `CLAMAV_REQUIRED=true` an upload is refused (503) when the scanner cannot be reached, otherwise it is accepted and marked `unscanned`. An infected file is refused (422) and not stored.

| Method and path | What |
|---|---|
| `GET /api/v1/media/{assetId}` | details and which eBooks use it (the business's staff, Super Admins) |
| `GET /api/v1/media/{assetId}/file` | the picture. The business's staff, Super Admins, and learners enrolled in a training whose **published** eBook uses it. Anyone else: 404 |
| `DELETE /api/v1/media/{assetId}` | delete (the business's staff). 409 `ASSET_IN_USE` with the list of eBooks while a draft or published eBook still uses it |

**Replace** a picture by uploading the new one, pointing the image block at the new `asset_id`, saving, then deleting the old one. **Ownership:** an asset belongs to the business of the person who uploaded it; another business's picture cannot be used (validation says so) or fetched.

**Crop** is display metadata only (`width`, `fit`, `aspect_ratio`, `position`); the server does not cut pictures, so there is no variants API. Tell us if the apps need physically cropped files and we will add `POST /media/{assetId}/variants`.

## 7. Security, in one place

- Enterprise Admins and providers can edit only their own business's trainings. A Super Admin cannot edit, only review. A learner cannot reach any author endpoint.
- Only a Super Admin can approve or ask for changes. Only an approved version can be published.
- Learners get the published copy only, after the enrolment, training-open, draft and lock checks the course already uses. The older JSON some eBook lessons keep in `content` is no longer returned to learners by `/content` or the single-lesson endpoint.
- Rich text is cleaned on every save; pictures are checked and scanned; picture files are served only to the people above; picture addresses in a page can only be image blocks, never raw tags.
- Saves carry a version check (409 `VERSION_CONFLICT`), a draft locked in review cannot be changed (409 `IN_REVIEW`), and every change is in the audit trail (`GET BASE/history`).

## 8. Before this goes live

1. Run the migrations in order. The eBook one is `c8f4a2d6b9e1`; it needs the payments migration `b7e3d9a1c5f2` (which needs the blog one `a6d2c8e4b1f7`) before it.
2. Pictures uploaded before this release have no asset record. Upload them again to use them in an eBook.
3. Configure the virus scanner (`CLAMAV_HOST`) in production, or accept `scan_status: skipped`.
4. eBooks that were saved as JSON in a lesson's `content` field are offered for import when the lesson is opened for editing; save once to move them. Nothing is converted automatically.
5. A duplicated training keeps its eBooks as drafts (nothing live, nothing approved).
6. Not included: eBooks in the offline-download list, search inside an eBook, PDF / ePub export, and telling enrolled learners when a new version is published.
