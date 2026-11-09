# Form builder: the common contract

For the web and mobile developers. One field model and one set of rules for the Event, Training and Blog form builders.

**Status:**
- **Blog**: fully on this model (builder, audit, review, media). See `docs/blogs-api.md` section 9.
- **Event and Training**: their existing builders, routes and stored data are unchanged and still work. In addition they now have the common contract (sections 11 and 12): read views for the three audiences, the
  field and section operations, the new field types, `enterprise_admin_only` visibility with the values kept out of public answers, review and submit.

The code lives in `app/form_builder/` (types, validation, URL inspection, builder operations, audience views). Each module is an adapter: it stores definitions, adds its protected fields and exposes endpoints.

## 1. The field

```json
{ "id": "6f1c…", "key": "event_date", "label": "Event date", "type": "date", "renderer": "date_picker", "placeholder": null, "help_text": "When it happens",
  "required": true, "enabled": true, "order": 2, "validation": { "not_in_past": true }, "options": [], "visibility": "published", "system": false }
```

| | |
|---|---|
| `id` | assigned once, never changes |
| `key` | the stable name values are saved under: 2 to 40 lowercase letters, digits, underscores, starting with a letter. **Never changes** once the field exists (422 `KEY_IMMUTABLE`) |
| `type` | one of the 14 below. **Never changes** (422 `TYPE_IMMUTABLE`), because saved values depend on it. Add a new field instead |
| `renderer` | which control to draw; fixed by the type |
| `enabled` | a switched-off field is not offered to writers or shown anywhere except in the builder; `required` only counts while enabled |
| `order` | the position in the section, from 1 |
| `visibility` | `published` (in the public and mobile APIs) or `enterprise_admin_only` (never public) |
| `system` | a protected field the module owns (see 5) |

### Field types

| `type` | `renderer` | Value | Rules in `validation` |
|---|---|---|---|
| `short_text` | `text_input` | text | `min_length`, `max_length` (up to 500, default 255), `pattern`, `format` (`email` or `phone`) |
| `rich_text` | `rich_text_editor` | formatted text | `min_length`, `max_length` (of the text, up to 100,000) |
| `long_text` | `textarea` | text | `min_length`, `max_length` (up to 10,000, default 5,000) |
| `dropdown` | `select` | one option | needs `options` |
| `radio` | `radio_group` | one option | needs at least 2 `options` |
| `checkbox` | `checkbox_group` | several options | needs `options`; `min_items`, `max_items` |
| `number` | `number_input` | number | `min`, `max`, `decimals` (0 to 6; 0 = whole numbers) |
| `date` | `date_picker` | `YYYY-MM-DD` | `min`, `max`, `not_in_past`, `not_in_future` |
| `time` | `time_picker` | `HH:MM` | `min`, `max` |
| `reference_url` | `url_input` | a link: `{url, provider, video_id, thumbnail_url}` | `allowed_providers`, `require_https`, `max_items` (a list) |
| `additional_images` | `image_uploader` | list of `{url, name, size, content_type, alt, caption}` | `min_items`, `max_items`, `max_file_size_mb`, `allowed_types` (jpg, jpeg, png, webp, gif) |
| `supporting_document` | `document_uploader` | list of file items | the same; types pdf, doc, docx, xls, xlsx, ppt, pptx, txt, csv |
| `video` | `video_input` | list of `{url, provider, video_id, thumbnail_url, ...}` | `min_items`, `max_items`, `max_file_size_mb`, `allowed_types` (mp4, webm, mov), `allowed_providers`, `allow_upload` |
| `tags` | `tag_input` | list of short labels | `max_items`, `max_length` |

`GET /admin/form-field-types` returns this table (every rule with its kind and bounds) so the builder screen needs no copy of it. `tags` is the one type beyond the thirteen the product asked for: Blog posts already have tags.
Field types are never turned into text, textarea or select in any answer.

**Options** (`dropdown`, `radio`, `checkbox`): `[{value, label}]`, up to 50. A repeated value or label is refused (`DUPLICATE_OPTION`); a type without options refuses them (`OPTIONS_NOT_ALLOWED`).

**Duplicates:** two fields with one `key` (409 `DUPLICATE_KEY`), two fields with one label in a section (409 `DUPLICATE_LABEL`), two sections with one title (409 `DUPLICATE_SECTION`).

### Checking values (what a writer sends)

Empty values are dropped (or refused when the field is required). Each rule has its own code in `errors[].code`: `TOO_SHORT`, `TOO_LONG`, `TOO_SMALL`, `TOO_LARGE`, `TOO_MANY_DECIMALS`, `TOO_FEW`, `TOO_MANY`, `INVALID_TYPE`,
`INVALID_FORMAT`, `INVALID_OPTION`, `IN_THE_PAST`, `IN_THE_FUTURE`, `HTTPS_REQUIRED`, `PROVIDER_NOT_ALLOWED`, `UPLOAD_NOT_ALLOWED`, `FILE_TYPE_NOT_ALLOWED`, `FILE_TOO_LARGE`, `DUPLICATE_ITEM`, and the URL codes below.
File size and type are checked from what the item says (`size`, `content_type`, the name); an upload made through the module's upload endpoint is checked on its real bytes there.

## 2. URLs

Every `reference_url`, `video` link and file address goes through one inspector, which also makes no network call:

- `provider`: `youtube`, `vimeo` or `external` (any other http or https address); an uploaded file's own path is `internal` (shown as `upload` for a video).
- YouTube: `watch?v=`, `youtu.be/`, `embed/`, `shorts/`, `live/` and `youtube-nocookie.com` all give the 11-character `video_id`, the normal address `https://www.youtube.com/watch?v=<id>` and
  `thumbnail_url` `https://img.youtube.com/vi/<id>/hqdefault.jpg`. Vimeo (`vimeo.com/<id>`, `player.vimeo.com/video/<id>`, channels) gives the numeric `video_id` and `https://vimeo.com/<id>`; **a Vimeo thumbnail
  needs a call to Vimeo and is not provided (`thumbnail_url: null`)**.
- Other addresses are normalized (scheme and host in lower case, the default port and the fragment removed).
- Rejected with a readable message: `UNSAFE_SCHEME` (anything but http and https: `javascript:`, `data:`, `file:`, `ftp:`, `//host`), `CREDENTIALS` (`user:pass@`), `PRIVATE_HOST` (localhost, `.local` and similar names,
  private, loopback, link-local and metadata-service IP addresses), `INVALID` (spaces, a missing website name, a bad port), `TOO_LONG` (over 2,000 characters), `INVALID_VIDEO` (a YouTube or Vimeo address that is not a video).
- A DNS name that points to a private address is **not** caught (no lookup is made); keep fetching of these addresses out of server code, or add a check where they are fetched.

## 3. The three views

The same form is answered three ways. The projection is applied on the server; it is not a client option.

| View | Who | Fields | Per field |
|---|---|---|---|
| `public` | public and mobile apps | enabled and `published` only | `id, key, label, type, renderer, order, options` |
| `enterprise_admin` | the Enterprise Admin writing | every enabled field, including `enterprise_admin_only` | the above plus `placeholder, help_text, required, enabled, validation, visibility`, and `protected` |
| `super_admin` | the form builder | every field, including disabled ones | everything, including `system` |

Saved **values** are filtered the same way: a public answer has `custom_fields` as a list of `{key, label, type, renderer, value}` for published, enabled fields only, with no internal file ids; an
`enterprise_admin_only` value (and its files and links) never appears in it, and a platform post cannot carry one.

## 4. Operations

Fields: add, edit, delete, duplicate, reorder, move to another section. Sections: add, edit, delete, reorder. Each change is applied to the **draft**, raises its `revision`, and is audited. Whole-definition saves still work.
Draft, review, submit, publish, activate and deactivate, version history and the audit history are described in `docs/blogs-api.md` section 9 for Blog (the same shapes will be used for Event and Training).

Positions are 1-based. A section with fields is deleted with `move_to` (409 `SECTION_NOT_EMPTY` otherwise). The last section cannot be deleted. Operations that fail leave the draft as it was.

## 5. Protected (system) fields

Each module marks the fields it cannot work without. A protected field cannot be deleted (409 `PROTECTED_FIELD`), re-keyed, re-typed, switched off, made optional, or taken out of the published content; its label, help text,
placeholder and position can change. A copy of a protected field is an ordinary field. Blog's protected fields: title, body (`content`) and category. The other built-in Blog fields can be switched off but not deleted (`CORE_FIELD`).
Event and Training: the protected fields are the module's existing domain-required core fields: **Event** title, description, category, start_date, end_date, pricing_type; **Training** title, category.

## 6. Errors

The standard body of the Blog module (`detail` plus `code`, and `errors` for validation) is used. Builder codes: `FIELD_NOT_FOUND` and `SECTION_NOT_FOUND` (404); `PROTECTED_FIELD`, `CORE_FIELD`, `KEY_RESERVED`,
`DUPLICATE_KEY`, `DUPLICATE_LABEL`, `DUPLICATE_SECTION`, `SECTION_NOT_EMPTY`, `SECTION_HAS_PROTECTED_FIELDS`, `STANDARD_FORM_FIXED`, `VERSION_CONFLICT`, `REVIEW_REQUIRED` (409); `VALIDATION_FAILED`, `INVALID_FORM`,
`INVALID_ORDER`, `LAST_SECTION`, `TOO_MANY_FIELDS`, `TOO_MANY_SECTIONS`, `FORM_NOT_READY` (422).

## 7. Versions and backward compatibility

- **The form model has its own version, `contract`, carried by the module's API path and docs.** Today there is one (this one). A change that removes or renames a field of an answer, or changes what a type means, is a new contract and is announced with a date; adding a field to an answer or a new rule is not.
- A form is versioned by publishing: a post (or event, or training) keeps the form version it was written with, and an old version is never changed. See `docs/blogs-api.md` section 9.3.
- **Older definitions** (the names before this model) are read in the new format by the application every time (the conversion gives the same result each time), and a migration rewrites them once.
- **What is a breaking change for existing Blog clients:** field `type` values changed (`text` is now `short_text`, `select` is `dropdown`, `multiselect` is `checkbox`, `url` is `reference_url`, `image` and `media_upload` are `additional_images`);
  `visible` is `enabled`; `visibility` is `published` / `enterprise_admin_only` (was `public` / `internal` with a separate `enterprise_only`); a core field can no longer be hidden from the public by a flag, and an
  `enterprise_admin_only` field is no longer available on platform posts; the old yes/no `checkbox` is a `radio`; media custom fields are lists of items (`[{url, ...}]`), not plain addresses; every list of fields has `id` and `order`.
  The Blog form builder had been in use for a few days when this changed. If a web client already built against the older shape, it must move to the new one together with this release.

## 8. Migration plan for existing forms

Blog (migration `j7a2c5e8b1d3`, after `i6e1a4d9f3c5`):
1. Adds the audit table and the form review columns (`j7a2c5e8b1d3`). A later migration, `k8b3d6f9c2e5`, adds the file delete and replace columns.
2. Rewrites every stored draft and every published version to the new field format. It is repeatable and keeps keys, so saved post values still match. A definition it cannot convert is left alone and logged (the
   application still reads the old shape for it).
3. In offline (`--sql`) mode nothing is rewritten; the application converts on read until the migration is run online.
4. Not done: post values saved for an old yes/no checkbox (`true` / `false`) are **not** rewritten to the radio's `yes` / `no`. Check whether any form used one before running (`select` forms with `type = 'checkbox'` and no options).

Event and Training: not started. They have their own tables (versions, assignments, audit) which already exist; their move to this model needs a field-by-field mapping that has not been decided (their current field
types and the product's list of protected fields are the inputs).

## 9. Rollout

1. Run `alembic upgrade head` on the test database; check `GET /admin/forms` and `GET /my/form?kind=blog` return the new format for an existing form.
2. Deploy the application. The web team's Blog form builder must use the new field shape in the same release (see 7).
3. The form builder has these settings: `BLOG_FORM_REQUIRE_REVIEW` (off by default) and the existing upload limits. Turn the review requirement on once the Super Admin screen has the Review and Submit buttons.
4. Keep the old definition column data (the migration only rewrites JSON in place); to roll back, run `alembic downgrade j7a2c5e8b1d3:i6e1a4d9f3c5`, which drops the audit table and review columns and leaves the rewritten
   definitions (the previous code cannot read them: restore from a database backup taken before step 1 if you need the old code back).

## 10. Media (built for Blog)

Details are in `docs/blogs-api.md` section 10. What a media field (`additional_images`, `supporting_document`, `video`) can do:

- **Files:** upload images, documents and videos (MP4, MOV, WebM); replace the content of a file under the same address; delete an unused file; list a business's files and see where each is used; metadata for every file
  (name, size, type, picture size, checksum, scan status).
- **Links:** a `video` field also takes YouTube, Vimeo and other links (section 2), returning the provider, video id and the YouTube thumbnail.
- **Checks:** the type is read from the content and must agree with the extension and with what the upload says it is; size limits per kind and per field; the virus scan when ClamAV is configured; a field's own
  `max_file_size_mb`, `allowed_types`, `min_items` and `max_items` are checked against the real stored files.
- **Access:** a file is private to its business (and Super Admins) until a published post uses it; a signed link gives temporary access to anyone who holds it; videos can be streamed with range requests.
- **Replace safety:** a file a published post uses cannot be replaced or deleted, so a published post never changes without approval.

## 11. Event and Training: building forms in the common contract

Routes under `/events/form-configuration/admin/{config_id}/common` and `/trainings/form-configuration/admin/{config_id}/common` (same auth as the existing form configuration routes). Every route changes the **draft**
(a published configuration forks a new draft first), raises its `etag`, withdraws a submitted review and is written to the configuration's audit log (`GET .../{config_id}/audit`, action `common_<action>`).

| Method and path (after `/common`) | What |
|---|---|
| `GET /field-types` | the types, renderers, options support, file types and rules; includes the module types `datetime` and `toggle` |
| `POST /sections`, `PATCH /sections/{id}`, `DELETE /sections/{id}?move_to=`, `PUT /sections/order` | add, edit, delete, reorder sections |
| `POST /fields` | `{section_id, position?, field}`; `change.field_id` is the new field |
| `PATCH /fields/{id}` | `{patch}`; by id or key; key and type never change |
| `DELETE /fields/{id}`, `POST /fields/{id}/duplicate`, `POST /fields/{id}/move`, `PUT /sections/{id}/fields/order` | delete, duplicate, move to another section, reorder |
| `GET /review`, `POST /submit` | the review checkpoint |

Answers are the builder view (section 12) with `etag` and `review_status`, plus `change`. Send the `etag` back as `base_etag` to refuse a change over someone else's (409 `VERSION_CONFLICT` with `current_etag`).

**How it is stored.** The stored shape (`source`, `core_key`, `stable_key`, `renderer`, `position`, `is_enabled`, `composite_config` ...) is kept. An operation writes back only what it changed, so a field nobody touched is stored
exactly as it was. A field made here is a custom field with an extra `field_type` (its explicit type) and, if set, `visibility`; its `renderer` is the older name where the type has one (`text`, `textarea`, `select`,
`multi_select`, `number`, `date`, `url`, `datetime`, `checkbox`) and otherwise the new renderer (`rich_text_editor`, `radio_group`, `time_picker`, `image_uploader`, `document_uploader`, `video_input`, `tag_input`).
Fields without a `field_type` keep their older checks; fields with one are checked and cleaned by the shared rules (section 1).

**Built-in (core) fields.** They cannot be deleted (409 `CORE_FIELD`; a protected one 409 `PROTECTED_FIELD`) or duplicated; only `label`, `placeholder`, `help_text`, `required` and `enabled` can be changed (anything else is
409 `CORE_FIELD`); a protected one cannot be switched off or made optional. A built-in field's key cannot be used for a new field (409 `KEY_RESERVED`).

**Switched-off fields.** This builder keeps them in the draft (the older save route, `PATCH /{config_id}`, drops switched-off fields and sections every time it saves, and so does publishing: a published version has
enabled fields only).

**Visibility and public answers.** A field with `visibility: enterprise_admin_only` keeps its values apart: when an event or training is created or updated, the values of such fields go to a separate column
(`private_custom_values`) and not into `custom_values`, which is part of the public answers. The public answers of the event and training therefore never hold them, and the `/form/common` view (section 12) shows
them only to the Enterprise Admin and the Super Admin. When an update sends no value for such a field its stored value is kept; sending `null` clears it. The visibility of a field is the one in the form version the
record was written with, so changing a field to `enterprise_admin_only` in a new version does **not** move values already saved under an earlier version out of the public column.

**Review and submit.** `GET /review` lists the problems the publish check finds, the changes against the published version, and `review_status`. `POST /submit` records that this exact draft was checked (422
`FORM_NOT_READY` if it has problems); any edit withdraws it. With `FORM_REQUIRE_REVIEW` on, the existing publish route needs a submitted draft (409 `REVIEW_REQUIRED`); off by default.

**Not changed, and decisions taken:** the existing routes and who may call them (Enterprise Admins and providers can still configure forms: the request for Super Admin only has not been applied, because it changes
existing behaviour); `datetime` and `toggle` are module types (section 12); versions, publish, activate, deactivate, retire, assignments and audit use the existing routes.

**Not built yet for Event and Training:** media handling in the form builder (they keep their own upload endpoints); a bulk migration (none is needed: forms are converted on read; the migration adds the review
columns and `private_custom_values`); `private_custom_values` is not copied by event duplication or templates (only `custom_values` is); Training's conditional field rules (`settings.visibility`) are kept untouched but not editable here.

## 12. Event and Training: the views (read only)

All answers are `{audience, configuration_id, version_id, version, definition, ...}` where `definition.sections[].fields[]` are common fields (section 1) filtered for the audience (section 3).

| Method and path | Who | What |
|---|---|---|
| `GET /events/form-configuration/admin/{config_id}/common?version_id=` | form builder auth (as the existing builder routes) | the draft (or `version_id`, or the latest published), every field including switched-off ones, `system` flags; `audience: super_admin` |
| `GET /events/form-configuration/active/common` | Enterprise Admin | the form they write with (resolved like `/form-configuration/active`): enabled fields with placeholders, help, rules, `required`, options, `core_key`, `composite_config`; `audience: enterprise_admin` |
| `GET /events/{event_id}/form/common` | anyone who may see the event | the form the event was written with and its custom values: `custom_fields: [{key, label, type, renderer, value}]`. The audience is decided by the server: public (enabled `published` fields and their values only), `enterprise_admin` (staff of the event's tenant), `super_admin` |
| `GET /trainings/form-configuration/admin/{config_id}/common`, `GET /trainings/form-configuration/active/common` | as above | the Training equivalents |
| `GET /trainings/{training_id}/form/common` | signed in | as the event one; for a reader, a **published** training only (404 otherwise) |

The built-in (core) fields' own values are in the event or training itself; these views carry the form and the **custom** values.

**Type mapping** (nothing is turned into generic text; a stored field keeps `legacy_renderer` for reference):

| Stored | Common `type` | `renderer` |
|---|---|---|
| renderer `text`, `textarea` | `short_text`, `long_text` | `text_input`, `textarea` |
| `select`, `multi_select` | `dropdown`, `checkbox` | `select`, `checkbox_group` |
| `number`, `date`, `time`, `url` | `number`, `date`, `time`, `reference_url` | as in section 1 |
| `image`, `document`, `video` | `additional_images`, `supporting_document`, `video` | uploader and video renderers |
| core `tags`; `primary_image`, `instructor_photo`; `gallery_images`; `videos`, `promotional_video`; `documents`; `start_time`, `end_time` | `tags`; `additional_images` (max 1); `additional_images`; `video`; `supporting_document`; `time` | |
| a core field picked from a list (category, subcategory, location, currency ...) | `dropdown`, with `options_source` | `select` |
| **no counterpart among the 14** (module types, `module_type: true`) | `datetime` (`datetime_picker`), `toggle` (a yes/no switch: stored `checkbox` renderer, requires_approval, randomise ...; `switch`), `venue`, `sessions`, `ticket_types`, `registration_fields` (their editors), `json` (`json_editor`) | |

**Decisions to confirm with the product team:** the `datetime` type (Event and Training dates and times are one value; the common list has Date and Time separately), the `toggle` type (the common list has no yes/no
field; the older Blog checkbox became a yes/no radio), and the composite built-in fields (venue, sessions, ticket types, registration fields) as module types.

## 13. Section visibility

A section has `visibility` (`published` or `enterprise_admin_only`) and `enabled`. An `enterprise_admin_only` section and **every field and value in it** is for Enterprise Admins only: it is left out of the public and
mobile form and out of the public values, and a field in it behaves as an Enterprise Admin only field for everything else (a Blog platform post cannot carry a value for it; an Event or Training value for it is stored in
`private_custom_values`). A switched-off section is for the builder only. Set both when adding a section (`POST .../sections`, `visibility`) or later (`PATCH .../sections/{id}`: `visibility`, `enabled`). Event and Training
store a section's `visibility` next to its older keys; a section without one is `published`.

## 14. Concurrency: ETag, If-Match and revisions

Every form answer carries an `ETag` header: Blog forms `"<revision>"` (the form's `revision`, which goes up with every save of the draft), Event and Training builder answers `"<etag>"` (a hash of the version's content).
Send it back as `If-Match` on any change (edit, section and field operations, submit, publish, activate ...) or in the body as `base_revision` (Blog) / `base_etag` (Event, Training); the body wins when both are sent.
`W/` is accepted and `*` means no check. If the form changed since:

```json
409 { "detail": "The form was changed by someone else after you opened it. Reload it, then apply your changes again", "code": "VERSION_CONFLICT", "current_version": 5 }
```
(`current_etag` for Event and Training) and nothing is changed. Blog posts use the same idea with their own `version` (`docs/blogs-api.md` section 11). Not sending a version means "overwrite", so clients that do not care keep working.

## 15. Lifecycle

The same words for every module, in `lifecycle` (read only) with `available_actions` on every form answer (Blog form items and details, Event and Training builder views):

| `lifecycle` | Meaning | Usual next actions |
|---|---|---|
| `draft` | never published, not submitted | edit, review, submit, publish, delete |
| `in_review` | submitted: reviewed, ready to publish, no edit since | publish (an edit sends it back to `draft`) |
| `inactive` | published, not offered to new records | activate, retire, edit, assign |
| `active` | published and offered | deactivate, edit, assign |
| `retired` | withdrawn for good (Blog: `POST .../retire`, `.../restore`; Event and Training: their existing `/retire`) | restore (Blog), delete when unused |

Editing a published form never changes it: the edit is a new draft (`has_unpublished_changes`) that becomes the next version when published. A form is retired only while inactive (409 `FORM_ACTIVE`); a retired Blog form
cannot be activated (409 `FORM_RETIRED`) and is not offered, but posts written with it keep their version and stay readable. `publish` is offered only when the draft was submitted if `BLOG_FORM_REQUIRE_REVIEW` /
`FORM_REQUIRE_REVIEW` is on.

## 16. Active published forms for public and mobile apps

No sign-in: Blog `GET /cms/blogs/forms/public?kind=`, Event `GET /events/form-configuration/public/active?enterprise_id=`, Training `GET /trainings/form-configuration/public/active?enterprise_id=`. The form assigned
to the business if there is one, else the active global form (404 when there is none); only enabled `published` fields and sections (section 3); `name`, `description`, `version`, `published_at`. A record's own form
and values: `GET /events/{id}/form/common`, `GET /trainings/{id}/form/common`.

## 17. Description and assignments

Every form has a `description`. Blog: `description` on create and update; assignments with `PUT /admin/forms/{id}/assignments` (`global`, or `selected` with `tenant_ids`) and `assignments` in the form detail
(tenant and business name). Event and Training: the configuration's `description`, and in the builder view `assignments: [{tenant_id, enterprise_id, business_name}]` (their existing `PUT .../{config_id}/assignments`
changes them). Which form a business gets: assigned to it first, else global, newest published wins; a retired or inactive form is never offered.

## 18. Existing records and backward compatibility

- A record keeps the form **version** it was written with. A live Blog post (and one waiting for approval) keeps it for good; a draft follows the active form and is moved to it when saved. An Event or Training keeps
  its `form_configuration_version_id`. Public answers for a record use that version's field labels, types and visibility.
- **Removing a field** from a later version does not delete saved values: they stay with the record, are not shown by the newer version and do not block submitting. A live post still shows them under the version it was
  published with.
- **Changing a field to `enterprise_admin_only`** or moving it into a private section affects records written with that version or later. Values already saved under an earlier version stay where they are
  (Event and Training: in the public column).
- Older definitions (the names before the common model) are converted on every read and rewritten once by the migration (Blog). Event and Training are converted on read; nothing is rewritten.
- The responses of the existing Event and Training form routes, and the stored data, are unchanged; the common contract is added next to them. The version of the contract is described in section 7.

## 19. Database performance and indexes

Indexes added for these routes (migration `m2d5f8b3a6c1`): Blog form audit `(form_id, created_at)`, Blog forms `(status, is_active, form_type)`, Blog posts `(form_id, form_version)`, Blog files
`(tenant_id, purpose, created_at)`, Event and Training form audits `(configuration_id, created_at)`. Existing: unique `(form_id, version)` on versions, `(form_id, tenant_id)` on assignments, the post and file foreign keys,
`events.form_configuration_version_id`. The migration uses plain `CREATE INDEX` (it blocks writes to the table while it builds: fine for these tables; for a very large `cms_blog_posts` create the index `CONCURRENTLY` by
hand first and the migration skips it).

What to know about cost (not load-tested; sizes are estimates):
- The forms list shows each form's post count and assignments with a query each: fine for a page of up to 100 forms; do not use it as a bulk export.
- Choosing the active form for a business reads the active forms and their assignments: a handful of rows.
- **A file is public while a live post uses it**, decided with a text search over the live posts' file lists and custom values. It is fine for thousands of live posts and becomes a scan on millions; the fix is a table
  of which post uses which file (not built). Signed links skip that check.
- Videos are read into memory to be checked (limit 100 MB per upload, `BLOG_MAX_VIDEO_MB`): limit concurrent uploads at the proxy.
- `GET .../diagnostics` counts everything and is for operators, not for a dashboard that refreshes every few seconds.

## 20. Monitoring

- **Logs:** every builder action writes one line to the `form_builder` logger: `form_builder module=<blog|event|training> form_id=<id> action=<action> ... actor=<id>`; failures are the normal error log. Alert on a
  rise of 5xx on `/cms/blogs/admin/forms*`, `/events/form-configuration*` and `/trainings/form-configuration*`, and on `VERSION_CONFLICT` (409) bursts (two people editing).
- **Diagnostics for operators (Super Admin):** `GET /cms/blogs/admin/forms/diagnostics` (forms by lifecycle, definitions still in the older format, posts pinned to a missing form version, files by scan status and kind),
  `GET /events/form-configuration/admin/common/diagnostics` and `GET /trainings/form-configuration/admin/common/diagnostics` (configurations by status and scope, active ones, drafts, drafts submitted for review, versions with
  Enterprise Admin only fields, records without a form version).
- **Audit:** every change is in the form's audit history (`GET .../audit`), who, when, what.
- **Things to watch:** `files.by_scan_status` with many `unscanned` or `skipped` (ClamAV down or not set up), `pinned_to_a_missing_form_version` above 0, `versions_in_older_format` above 0 after the migration, and
  `records.without_form_version` growing.
- Not built: a metrics endpoint (Prometheus) or dashboards: the numbers above come from the diagnostics routes and the logs.

## 21. Rollout, migration steps and rollback

Order and checks:
1. **Back up the database.** Take a snapshot or dump before the migrations (the Blog definition rewrite cannot be undone by `alembic downgrade`).
2. `alembic upgrade head` on a copy of production first. Migrations for this work, in order: `g4c9e2b7d1a3`, `h5d0f3c8e2b4` (Blog), `i6e1a4d9f3c5` (Event Discussions, unrelated), `j7a2c5e8b1d3` (Blog audit, review, definition rewrite),
   `k8b3d6f9c2e5` (Blog file delete and replace), `l9c4e7a1d2f6` (Event and Training review tracking, private values), `m2d5f8b3a6c1` (indexes). Run `j7a2c5e8b1d3` **online** (not `--sql`) so the rewrite happens.
3. Check with the diagnostics routes: `drafts_in_older_format` and `versions_in_older_format` are 0; forms by lifecycle look right; `GET /cms/blogs/forms/public?kind=blog` and `GET /my/form?kind=blog` answer in the new shape.
4. Deploy the application. The web and mobile teams must ship their form code in the same release (types and field names changed for Blog, section 7); the Event and Training routes they use today are unchanged.
5. Leave `BLOG_FORM_REQUIRE_REVIEW` and `FORM_REQUIRE_REVIEW` off until the Super Admin screen has Review and Submit; then turn them on.
6. Smoke test (staging): create a form, add one field of each type, submit, publish, create a post/event/training with values, read it as public, Enterprise Admin and Super Admin, upload and replace a file, retire and restore a Blog form.

Rollback:
- **Application only** (no schema change needed): redeploy the previous build. The previous Blog code **cannot read** the rewritten definitions: if you must run it again, restore the Blog form tables from the backup
  taken in step 1 (or re-run the old code against a restored database). Event and Training: the previous code ignores the added keys and columns and keeps working.
- **Schema:** `alembic downgrade` for each revision drops what it added (`m2d5f8b3a6c1` drops the indexes; `l9c4e7a1d2f6` drops `private_custom_values`: **this deletes enterprise-only values saved there**, export them first;
  `k8b3d6f9c2e5`, `j7a2c5e8b1d3` drop the file columns, audit table and review columns). The definition rewrite is not reversed.
- Files are on disk (`UPLOAD_DIR/blogs`): a rollback does not delete them. Deleted files lost their bytes (their records remain).

## 22. Requirement checklist

| Requirement | Where | Status |
|---|---|---|
| 13 field types with explicit names and renderers | sections 1, 12 | Blog, Event, Training (Event and Training older fields are mapped on read; new fields use the types). Plus `tags`, `datetime`, `toggle` |
| Field response attributes | section 1 | yes, all three audiences (section 3) |
| Field and section operations | `docs/blogs-api.md` 9.5, section 11 | Blog, Event, Training |
| Save draft, review and submit, publish, activate and deactivate, versions, audit | blogs-api 9, section 11, 15 | Blog and Event/Training (their own routes plus the common ones) |
| Validation rules | section 1 | yes (shared rules) for Blog and for new Event/Training fields; older fields keep their older checks |
| URL behaviour | section 2 | yes |
| Media: uploads, replace, delete, scan, signed links, range | section 10 | **Blog only**; Event and Training keep their own upload routes |
| Visibility, Enterprise Admin only values never public | sections 3, 11, 13 | Blog, Event, Training for **new** records; older saved values are not moved |
| Protected fields | sections 5, 11 | yes |
| Super Admin only form configuration | | **not applied** to Event and Training (it changes existing behaviour); Blog forms are Super Admin only |
| Section visibility | section 13 | yes |
| Description and assignments | section 17 | yes |
| ETag / If-Match / revisions | section 14 | yes |
| Lifecycle statuses | section 15 | yes (computed, same words) |
| Backward compatibility for existing records | section 18 | yes (version pinning); tests cover Blog |
| Active published forms for public and mobile | section 16 | yes |
| Indexes, monitoring, rollback, migration steps | sections 19 to 21 | documented and built as stated there; not load-tested; no metrics endpoint |
| Tests | `tests/test_form_builder_core.py`, `test_blog_form_builder.py`, `test_blog_media.py`, `test_form_common_view.py`, `test_form_common_write.py`, `test_form_builder_lifecycle.py` | on an in-memory database; none on PostgreSQL |

## 23. What is not here yet

- **Media for Event and Training form fields** (replace, delete, signed links, video uploads). Blog has it.
- **Super Admin only form configuration** for Event and Training.
- **Moving already-saved values** out of the public column when a field becomes enterprise-only in a later version.
- **A file-usage table** (for the public-file check at scale), video processing (thumbnails, transcoding), object storage, revoking signed links, a metrics endpoint.
- **PostgreSQL, ClamAV, a real browser:** none were used; tests run on an in-memory database.
