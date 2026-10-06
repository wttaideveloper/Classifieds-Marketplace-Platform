# Event media upload contract

For the Event form's **Primary Image, Gallery Images, Videos and Documents**.

## Is there an existing upload API for Events?

Not one you should use. `POST /api/v1/uploads/` can store a file, but it is for training authoring and has no
Event rules: any logged-in user may call it, one flat 100 MB limit for everything, no per-field types or counts,
no tenant ownership, and its download route (`GET /api/v1/uploads/{folder}/{file}`) needs no login at all.
Event create/update also accepted any string in these fields. So there is now a dedicated contract, below.

## Summary

- Upload each file **once**, to `POST /api/v1/events/media`. You get back an *asset* with a hosted https `url`.
- The Event create/update JSON **keeps storing plain URLs** (documents: small objects) — the same fields, the same
  shape as before, so existing Events, lists and mobile screens keep working. No media ids or storage keys are stored.
- On save the server checks every uploaded-file URL belongs to the caller's tenant and fits the field, and rewrites it
  into canonical form. **Referencing an upload from a saved Event is what "completes" it.** There is no separate
  init/complete call: files go straight to this API's own disk, so there is no pre-signed-URL handshake. (If storage
  later moves to S3/CDN, init + complete endpoints would be added; the Event JSON would not change.)

## Endpoints

Base: `https://chat.wisdomtooth.tech/api/v1/events/media`

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /policy` | none | Allowed MIME types, extensions, max size and max count per field |
| `POST /` | login: Enterprise Admin / provider of the tenant, or Platform Super Admin | Upload one file |
| `GET /{asset id}.{ext}` | see "Who can read a file" | Display / download |
| `DELETE /{asset id}` | same as upload | Discard an upload no Event uses |

### Upload

`POST /api/v1/events/media/` — `multipart/form-data`, `Authorization: Bearer <token>` (or the session cookie)

| Form field | Required | Notes |
|---|---|---|
| `file` | yes | the file |
| `field` | yes | `primary_image` \| `gallery_images` \| `videos` \| `documents` |
| `enterprise_id` | Super Admin only | which enterprise the file belongs to. Tenant staff may omit it; if sent it must be their own |
| `client_ref` | no (recommended) | your unique id for this attempt, ≤64 chars — makes retries safe |

```
curl -X POST https://chat.wisdomtooth.tech/api/v1/events/media/ \
  -H "Authorization: Bearer $TOKEN" \
  -F field=documents -F client_ref=form-7f3a-doc-1 -F "file=@Agenda.pdf;type=application/pdf"
```

`201 Created` (`200 OK` with the *same* body when `client_ref` repeats):

```json
{
  "id": "7d2c1c3e-0b0f-4f3b-9d6e-5f6a0c8c9a11",
  "field": "documents",
  "url": "https://chat.wisdomtooth.tech/api/v1/events/media/7d2c1c3e-0b0f-4f3b-9d6e-5f6a0c8c9a11.pdf",
  "name": "Agenda.pdf",
  "size": 482113,
  "type": "application/pdf",
  "attached": false,
  "expires_at": "2026-10-07T10:15:00"
}
```

Errors: `401` not logged in · `403` not staff / foreign enterprise · `413` over the size limit · `415` type, extension or
file contents not allowed for that field · `400` empty file · `422` unknown `field`, or Super Admin without `enterprise_id`.

## What Event create / update carries

Same endpoints as today (`POST /events/`, `PUT /events/{id}`); only these four fields matter:

```json
{
  "primary_image":  "https://chat.wisdomtooth.tech/api/v1/events/media/<id>.png",
  "gallery_images": ["https://chat.wisdomtooth.tech/api/v1/events/media/<id>.jpg"],
  "videos":         ["https://chat.wisdomtooth.tech/api/v1/events/media/<id>.mp4"],
  "documents": [
    { "id": "<id>", "url": "https://chat.wisdomtooth.tech/api/v1/events/media/<id>.pdf",
      "name": "Agenda.pdf", "size": 482113, "type": "application/pdf" }
  ]
}
```

- Send `url` from the upload response. For `documents` you may send the whole asset object or just the url — the server
  stores `id/url/name/size/type` from **its own record** and ignores whatever name/size/type the client sent.
- Plain `https://` URLs (Pexels, S3, a CDN) are still accepted anywhere. `http://` is stored as `https://`. Anything else
  (`javascript:`, `data:`, relative paths, other schemes) → `422`.
- **PUT is "set what you send":** omit a field to leave it unchanged; send the **full** list to replace it; send `null`
  (`primary_image`) or `[]` (lists) to clear it.
- A bad request returns `422` with every problem at once:
  `{"detail": {"message": "Invalid event media", "errors": ["gallery_images: at most 10 allowed, got 11", ...]}}`.

## Limits (also available live from `GET /policy`)

| Field | Types (and extensions) | Max size | Max count |
|---|---|---|---|
| `primary_image` | image/jpeg (jpg, jpeg), image/png, image/webp | 5 MB | 1 |
| `gallery_images` | the above + image/gif | 5 MB each | 10 |
| `videos` | video/mp4 (mp4, m4v), video/webm, video/quicktime (mov) | 100 MB each | 3 |
| `documents` | pdf, doc, docx, xls, xlsx, ppt, pptx | 10 MB each | 10 |

The declared MIME type, the file extension **and the file's actual first bytes** must all agree (a renamed file is
rejected). Counts are enforced on Event save, for external URLs too. Proxy body-size limits in `deploy/*.conf.example`
(310 MB) already cover the 100 MB video cap.

## Ownership and tenancy

- Each file is owned by the **tenant of the uploader** (or of the enterprise a Super Admin names).
- On Event create/update, an uploaded-file URL is accepted only if its owner is the **Event's owning tenant**, and its
  type is allowed in the target field. A file from another tenant, a file that doesn't exist, and a wrong-type file are
  all `422`; the first two give the same message so file ids can't be probed across tenants.
- A tenant id in a request body is never trusted; the Event's tenant comes from the Event/Enterprise record.

## How detail, list and approval APIs return assets

They return exactly what was stored, so no extra call is needed to render:

| Field | Returned as |
|---|---|
| `primary_image` | url string (or `null`) |
| `gallery_images`, `videos` | array of url strings |
| `documents` | array of `{id, url, name, size, type}` for uploaded files (older / external entries may be a bare url string or `{url, name?, size?, type?}` — handle both) |

Render images/videos from `url` directly; offer documents as downloads using `name` and `url` (the response carries
`Content-Disposition: attachment` with the original file name). The same `GET /events/{id}` payload is what the
Super Admin approval screens receive (`/admin/events/...` use the same fields), so reviewers see the same urls.

### Who can read a file

| File | Anonymous | Any logged-in user | Owning tenant staff | Platform Super Admin |
|---|---|---|---|---|
| Image used as primary image / gallery of a **published** Event | yes (cacheable, plain `<img>` works) | yes | yes | yes |
| Video / document of a **published** Event | no (401) | yes | yes | yes |
| Anything on a **draft / pending-approval / cancelled** Event, and uploads not yet saved | no (401) | no (403) | yes | yes (approval review) |

## Removal, replacement, retries, abandoned uploads

- **Remove / replace** a file by saving the Event without it (or with a different url). The old file is not deleted at
  once — it gets a 24 h grace period (so cached pages don't break), then is deleted, **unless some Event still uses it**
  (e.g. a duplicated Event).
- **Discard before saving:** `DELETE /events/media/{id}` removes an upload no Event references (`409` if one does).
- **Retries:** send the same `client_ref` when repeating an upload whose result you did not see; you get the original
  asset back (`200`) instead of a second copy. Saving the Event is naturally repeatable (PUT replaces).
- **Abandoned uploads** (never put into a saved Event) are deleted 24 h after upload; `expires_at` in the response says
  when. Cleanup runs opportunistically on later uploads and never deletes a file an Event references.
- Deleting an Event is a soft delete; its files stay until the Event stops referencing them.

## Deploy notes

- `alembic upgrade c5e8a1f3d7b2` — adds the `event_media` table.
- Files live under `UPLOAD_DIR/events/` — keep `UPLOAD_DIR` on a persistent volume (`docker-compose.prod.yml` sets `/app/uploads`).
- Set `PUBLIC_MEDIA_BASE_URL=https://chat.wisdomtooth.tech` so returned urls are https (see the HTTPS-only media note).
- Existing Events are untouched: their stored urls keep working; only new saves are validated.
