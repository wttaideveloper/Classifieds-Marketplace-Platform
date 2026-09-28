# Training form settings: backend contract

## Configuration endpoints

Base: `/api/v1/trainings/form-configuration/admin`
(alias: `/api/v1/admin/training-form-configurations`).

| Method/path | Behavior |
| --- | --- |
| POST `/` | Create draft, validate metadata and condition references |
| PATCH `/{config_id}` | Update draft; editing published configuration forks a draft |
| GET `/{config_id}` | Return draft and published versions including settings |
| POST `/{config_id}/publish` | Validate again and publish; response now also includes `sections` |
| GET `/{config_id}/versions` | Version history including settings |
| GET `/{config_id}/versions/{version_id}` | Immutable historical settings |
| POST `/{config_id}/activate` | Activate published form; existing activation summary unchanged |

Enterprise reads:

- `GET /api/v1/trainings/form-configuration/active`: resolved active tenant/global form for new Trainings.
- `GET /api/v1/trainings/{training_id}/form-configuration`: Training's historical form version, with the existing active fallback for legacy Trainings without a version.
- Training create/update: `POST /api/v1/trainings/` and `PUT /api/v1/trainings/{training_id}`; existing `/api/v1/courses` aliases also apply.

Configuration mutations remain Super Admin-only. Existing tenant ownership guards remain in place.

`composite_config` is an **open JSON object**, not a closed nested schema. Arbitrary keys are accepted and returned unchanged. The server explicitly validates and enforces the recognized `frontend_settings.visibility`, `frontend_settings.upload`, and `frontend_settings.allow_custom_value` keys; other metadata does not create additional server behavior. No migration is needed.

## Category/Subcategory options: parent linkage and custom ("Other") entry

`category` and `subcategory` are core fields (`FIELD_REGISTRY`) usable with `renderer: "select"`. `category` can never be disabled (`required_by_domain`) — `subcategory` is fully configurable. Both existed before this addition; this section documents two new, additive pieces of the same field configuration, requested because the frontend team found nothing in Swagger describing them.

**1. Subcategory belongs to a category — `parent_value` on each option.** Add `parent_value` (a category option's `value`) to a subcategory option:

```json
{
  "core_key": "subcategory", "renderer": "select", "required": false, "is_enabled": true,
  "options": [
    {"value": "yoga", "label": "Yoga", "position": 1, "parent_value": "wellness"},
    {"value": "nutrition", "label": "Nutrition", "position": 2, "parent_value": "wellness"},
    {"value": "fire_safety", "label": "Fire Safety", "position": 1, "parent_value": "safety"}
  ]
}
```

Filter the subcategory dropdown client-side to options whose `parent_value` equals the selected category's value. The server independently re-checks this on Training create/update: if the submitted `subcategory` matches a *known* option whose `parent_value` disagrees with the submitted `category`, the request is rejected — `400 {"detail": "Subcategory 'fire_safety' does not belong to category 'wellness'"}`. A subcategory field with no `parent_value` set on any option (legacy/unlinked configs) skips this check entirely — fully backward compatible.

**2. Allow a custom ("Other") value — `frontend_settings.allow_custom_value`.** Set on the field's `composite_config`:

```json
{
  "core_key": "subcategory", "renderer": "select",
  "composite_config": {"frontend_settings": {"allow_custom_value": true}},
  "options": [{"value": "yoga", "label": "Yoga", "position": 1, "parent_value": "wellness"}]
}
```

- **How it's configured:** `composite_config.frontend_settings.allow_custom_value: true` on either `category` or `subcategory` (or both, independently) — the same field-level toggle location as `visibility`/`upload`.
- **How Enterprise Admin submits a custom value:** No special wrapper, no separate flag, no `"Other"` sentinel string. Show an "Other" choice in the dropdown; when picked, submit whatever the admin types as the plain `category`/`subcategory` string on the Training payload, exactly like any configured option's value.
- **How it's validated:** when `allow_custom_value` is `true`, a submitted value that is *not* in `options` is accepted rather than rejected (normally, submitting a select value outside `options` is `400 Invalid select value for '<label>'`). A custom subcategory value skips the `parent_value` linkage check above — it isn't tied to any category. When `allow_custom_value` is unset/`false` (the default), behavior is unchanged: only listed option values are accepted.
- **Backward compatibility:** omitting `allow_custom_value` keeps the existing strict allowlist behavior. Existing Training records with free-text `category`/`subcategory` (from before any options were configured, or from a custom "Other" entry) remain readable and stay visible even if they're no longer in the current `options` list — the read endpoints return the Training's stored value as-is; only *new* create/update submissions are validated against the currently active configuration.

## Field request and resolved response

Include this field inside `sections[].fields[]` (the same metadata is returned there):

```json
{
  "id": "online_handout",
  "stable_key": "online_handout",
  "source": "custom",
  "label": "Online handout",
  "renderer": "document",
  "value_type": "string",
  "required": true,
  "composite_config": {
    "frontend_settings": {
      "visibility": {
        "field_key": "delivery_mode",
        "operator": "equals",
        "value": "online"
      },
      "upload": {
        "allowed_mime_types": ["application/pdf"],
        "max_file_size_mb": 20
      }
    }
  }
}
```

The same form must contain `delivery_mode`. Core media fields supported are `primary_image`, `gallery_images`, `promotional_video`, `videos`, `documents`, and `notes_pdf_url`; custom media renderers are `image`, `document`, and `video`. A custom URL value can be submitted as `{"custom_values":[{"field_id":"online_handout","value":"/api/v1/trainings/upload/<stored_name>"}]}`.

## Conditions and validation

- `field_key` resolves a core machine key, custom stable key, or field ID in the same configuration. Labels are not a separate reference namespace.
- `equals` / `not_equals`: compare typed JSON values to `value` (required for these operators).
- `has_value` / `is_empty`: empty means null, empty string, empty array or empty object. `false` and `0` count as values. Whitespace is not trimmed.
- Unknown references/operators, ambiguous identities, self-references and cycles are rejected with HTTP 400 at create/update/publish.
- A disabled section/field is not visible. A condition whose controller is hidden is false, even if that controller retains a value.
- **Hidden values are retained**, including submitted custom values, and returned normally. Hiding is a form rule, not a confidentiality boundary. Required checks, custom value types/options, configured length/pattern/range checks and upload-policy checks are skipped while hidden. When the field becomes visible, its retained value must pass validation.
- Training updates evaluate merged persisted core data plus the patch. An omitted `custom_values` retains previous values and still gets revalidated when a controlling field changes. A supplied `custom_values` collection retains its existing replacement semantics.
- Updates use the Training's stored configuration version, not a newly published active version.
- Base API schema/type constraints and domain invariants still apply. Domain-required `title` and `category` cannot be conditionally hidden.

Examples: missing required visible custom field returns 400 `{"detail":"Required custom field missing: Online handout"}`; circular configuration returns 400 `{"detail":"Circular visibility conditions are not allowed"}`.

## Uploads

Preferred endpoint: `POST /api/v1/trainings/upload` (multipart; 201).

| Part | Meaning |
| --- | --- |
| `file` | File bytes |
| `field_key` | Configured core key, stable key or ID |
| `purpose` | `image`, `lesson_video`, `lesson_document`, or `lesson_pdf`; may be inferred |
| `training_id` | On edit, use this Training's historical form; ownership is checked. Omit for active tenant form |

Example multipart parts: `field_key=online_handout`, `purpose=lesson_pdf`, `file=@handout.pdf`. On edit also send `training_id=<UUID>`. Do not send a client-selected configuration/version or size limit; the server resolves these.

Successful response retains its existing shape:

```json
{
  "url": "/api/v1/trainings/upload/<stored_name>",
  "name": "handout.pdf",
  "size": 12345,
  "type": "application/pdf",
  "purpose": "lesson_pdf"
}
```

The URL can be absolute when `PUBLIC_API_BASE_URL` is configured. `field_key` is required when the resolved form contains any upload policy. Invalid/disabled/non-media field keys are rejected. Conditional visibility is evaluated when saving the Training; files may be uploaded before the rest of the form has been submitted.

`POST /api/v1/uploads/` also accepts `field_key`, `training_id`, and `purpose`. Policy resolution applies when those field/Training parts are supplied or `folder` is `training`, `trainings`, or `courses`. Its existing generic allowlist and 100 MiB cap also apply; use the preferred endpoint for text documents or larger permitted videos.

The configured MIME allowlist is exact, lowercase MIME names, not wildcard patterns. Supported configured formats:

- Images: `image/jpeg`, `image/png`, `image/gif`, `image/webp`.
- Video: `video/mp4`, `video/quicktime`, `video/webm`, `video/ogg` (Theora), `video/x-msvideo`.
- Documents: `application/pdf`, `text/plain`, and the standard DOCX/XLSX/PPTX OpenXML MIME types.

MIME types must match the configured field's media kind. Size must be a positive finite number; MiB units are used (`1 MB = 1,048,576 bytes`). Effective cap is the smaller of field configuration and the existing server purpose cap. Omitting either upload setting keeps the corresponding server default. Declared MIME is checked against file signatures/content; images are parsed, OpenXML containers are inspected, and PDF/video signatures are checked. This is format checking, not antivirus scanning or full PDF/video decoding.

Training create/update revalidates **actual local stored files** for visible fields with upload policies, including URLs produced through generic uploads. Client-supplied `size`/`type` metadata is not trusted. External URLs are rejected for these fields because their contents cannot be verified by the local upload service. Legacy fields with no upload policy keep existing URL behavior.

| Status | Example error |
| --- | --- |
| 400 | `field_key is required for configured Training uploads` |
| 400 | `Upload purpose does not match configured field type` |
| 403 | Training belongs to another tenant / management access denied |
| 404 | Missing Training, configuration or referenced stored file |
| 413 | `File exceeds configured maximum of 20971520 bytes` |
| 415 | `File format is not allowed by the configured field upload policy` |
| 415 | `Declared MIME type does not match file content` |
| 422 | Invalid multipart/Training request schema, e.g. invalid UUID or purpose enum |

Errors use `{"detail":"..."}` except FastAPI request-schema errors, which retain its usual validation-error array. Rejected configured uploads are not saved.

## Compatibility

Configurations without `frontend_settings` need no conversion: enabled fields are visible, previous required/domain rules apply, and legacy uploads retain server defaults. Arbitrary existing `composite_config` metadata remains intact. The new `sections` member on publish responses and new upload form parts are additive. No frontend files or moderation transitions are changed.
