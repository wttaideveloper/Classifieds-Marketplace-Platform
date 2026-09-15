# Event templates and lifecycle: backend contract

## Scope

Template compatibility is implemented in this change. Lifecycle behavior below documents the existing backend; recommendations are explicitly marked. Session meeting integration (point 2) is deferred for discussion.

## 1. Event templates

### Create and update

`POST /api/v1/events/templates` returns **201**. `PUT /api/v1/events/templates/{template_id}` supports partial updates and returns **200**. Both require the `admin` or `provider` role. Existing tenant ownership rules apply.

Example create request (replace example UUIDs with real IDs):

```json
{
  "name": "Annual summit",
  "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
  "configuration_id": "550e8400-e29b-41d4-a716-446655440001",
  "configuration_version_id": "550e8400-e29b-41d4-a716-446655440002",
  "template_data": {
    "core_values": {
      "title": "Annual summit",
      "description": "Community gathering",
      "category": "Education"
    },
    "custom_values": [
      {"field_id": "dietary-preference-field-id", "value": "Vegetarian"}
    ],
    "composites": {
      "venue": {"name": "Main hall", "city": "Chennai"},
      "sessions": [{"title": "Welcome", "speaker": "Host"}]
    }
  }
}
```

Responses contain `id`, `tenant_id`, `enterprise_id`, `name`, `configuration_id`, `configuration_version_id`, `template_data`, and `created_at`. GET template/list responses expose the same provenance fields.

- Provenance is optional for legacy compatibility. Supply the source version for new configurable-form templates. A version must exist; its configuration ID is derived when omitted, and a mismatched pair returns 400.
- Provenance records where values came from. It does **not** select the new Event's form.
- Custom values use `field_id` (or `id`) and `value`; `stable_key` can identify a field when an ID is unavailable. Labels are never identifiers.
- Legacy flat `template_data`, such as `{"title":"Summit","sessions":[]}`, remains supported. Embedded `form_configuration_id` and `form_configuration_version_id` can supply provenance.
- Updating template data replaces that JSON object. Omitted provenance fields retain their previous values. Enterprise ownership cannot be changed through template update.

### Apply

`POST /api/v1/events/templates/{template_id}/apply`

Requires `admin` or `provider`. Request body is `{}`, or optional `tenant_id` / `enterprise_id`. It returns **201** with an EventResponse, including:

```json
{
  "id": "new-event-uuid",
  "status": "draft",
  "title": "Annual summit",
  "form_configuration_id": "current-configuration-uuid",
  "form_configuration_version_id": "current-version-uuid",
  "custom_values": [{"field_id": "current-field-id", "value": "Vegetarian"}]
}
```

This is a response excerpt, not the entire EventResponse.

Server behavior:

1. Resolve the tenant's current active published form: selective tenant assignment, then global, then active legacy/default. If none is active, return 404 and create nothing.
2. Match core values by registry `core_key` and compatible type/renderer. Never copy Event identity, lifecycle flags, review notes, or old form IDs as reusable core values.
3. Match custom values by field identity or stable key. Remap to the current field ID. Ignore removed/disabled fields, incompatible types/renderers, ambiguous matches, and values invalid under the new options/validation.
4. For composites, retain only enabled sub-fields that exist in both structures when provenance is available. Nested paths, such as `coordinates.lat`, are mapped individually. Removed sub-fields are discarded and newly added sub-fields remain empty. Sessions receive fresh IDs.
5. Create a fresh draft linked to the **current** configuration/version. Missing values remain null or absent; the template itself is unchanged.

Without historical provenance, legacy values are checked against the target form and known field identities/keys. Historical structure cannot be inferred from labels.

### Complete and submit

The UI should open the created draft, fetch `GET /api/v1/events/{event_id}/form-configuration`, and render that pinned version. Save changes with `PUT /api/v1/events/{event_id}`. Draft custom values can be saved incrementally.

Submit through `POST /api/v1/events/{event_id}/resubmit` (supports draft/needs_revision) or `PATCH /api/v1/events/{event_id}/status` with `{"status":"pending_approval"}`.

Submission/approval/publication checks domain requirements, configured required core/custom fields, and required composite sub-fields. A generic Event update into a submitted state also runs the guard. Missing required values return **400** and do not commit the transition.

Examples of validation responses:

```json
{"detail":"Required field 'Diet' is missing"}
```

```json
{"detail":"Required sub-field 'venue.city' is missing"}
```

Missing domain values produce `{"detail":{"code":"EVENT_FORM_INCOMPLETE","errors":[...]}}`. Other errors: 403 tenant mismatch; 404 missing template/no active form; 400 invalid provenance or malformed mapping data; 422 invalid typed request fields.

**Existing Event edit continues to use its historical version.** If the active form changes after a template draft is created, that draft remains linked to the version selected at creation.

### Migration and rollout

Apply Alembic revision `w4x5y6z7a8b9` before deploying the updated application (`alembic upgrade head`). It adds nullable template provenance columns, backfills valid legacy embedded UUIDs, and allows incomplete drafts to have null title/category/dates. Event response title/category are consequently nullable; normal Event creation and submission retain required-value validation.

The migration is supplied but has not been executed against a live database. Downgrade requires completing incomplete drafts first; it does not fabricate values or delete drafts.

## 2. Session meetings

Deferred at the user's request. No provider integration contract or connection-support claim is made in this change.

## 3. Lifecycle ownership and Web UI actions

### Archive

`POST /api/v1/events/{event_id}/archive` returns **200 EventResponse**.

| Question | Existing backend behavior |
| --- | --- |
| Who can use the dedicated endpoint? | `admin` and `provider`. The dedicated route does not accept `super_admin`; that role can use the generic status endpoint. |
| Allowed source statuses | `approved`, `draft`, `published`, `completed`, `suspended`, `cancelled`. Other transitions return 400. |
| Effect | Changes `status` to `archived`; does not set `is_deleted`. |
| Difference from cancel | Cancel changes status to `cancelled`, has cancellation side effects, and supports returning to draft with reapproval. |
| Difference from delete | DELETE soft-deletes (`is_deleted=true`) and also sets status to `archived`. |
| Hidden from normal lists? | No. Default list filtering excludes soft-deleted Events, but does not exclude archived status. |
| Restore/unarchive? | No dedicated restore route and no outgoing transition from `archived`. Do not expose Restore for archived Events. |

**UX recommendation:** an Enterprise Admin Archive action can be shown for allowed statuses. If product policy intends Archive only for completed/cancelled Events, restrict the UI to those statuses and schedule a matching backend policy change; the current API is broader. Do not describe Archive as reversible or as automatically hiding the Event.

### Admin notes

`GET /api/v1/events/{event_id}/admin-notes` accepts `admin` and `provider` and returns **200**:

```json
{
  "event_id": "event-uuid",
  "title": "Annual summit",
  "status": "needs_revision",
  "last_admin_notes": "Please clarify the venue details."
}
```

- These are the latest reject/request-changes comments, not an independent note-editing system.
- Platform moderation routes write them: `POST /api/v1/admin/events/{event_id}/reject` and `/request-changes`, with `{"reason":"..."}`. These use `get_current_super_admin`.
- Request Changes requires a reason; Reject currently permits an absent reason. A nonempty reason is stored on the Event and in the status-change audit record.
- There is no separate create/update admin-notes endpoint. The generic status endpoint does not forward a notes field.
- Yes: Enterprise Event detail should show this feedback when rejected or needing revision, with an edit/resubmit flow. The value can remain present after status changes; it is not automatically cleared.
- Some route descriptions still refer to Enterprise Admin acting as Super Admin for testing. The generic status route also permits `admin` to approve/reject/request changes. A strict Platform-only moderation boundary is therefore **not uniformly enforced** by all existing routes. Do not use those stale descriptions as the product permission policy.

### Auto-complete

`POST /api/v1/events/auto-complete?enterprise_id={optional_uuid}` uses `get_current_admin`, which permits `admin` and `super_admin` (plus the existing development-user fallback).

- Bulk-transitions non-deleted **published** Events whose `end_date` is before server UTC now to `completed`.
- With `enterprise_id`, it filters to that enterprise. Without it, it processes all matching Events; the function does not infer the caller's tenant scope.
- Returns **200** `{"auto_completed":3}` and writes audit records. Repeating after completion does not reprocess those Events.
- No scheduler wiring invoking this service was found in the repository. The endpoint does not schedule itself.

**UX recommendation:** treat this as a backend maintenance/operations action and wire it to a controlled scheduler. Do not put an Auto-complete button in Enterprise Event detail or trigger it automatically from a page load. Tenant scoping and scheduler ownership need an explicit policy before exposing this bulk action to Enterprise users.

## Validation

Regression coverage includes legacy/new template mapping, current-form selection, stable custom identities, disabled/removed fields, type/option changes, nested composites, provenance mismatch, missing active forms, fresh Session IDs, and required-field submission guards. Existing Event form tests also run. Live PostgreSQL migration verification remains outstanding.
