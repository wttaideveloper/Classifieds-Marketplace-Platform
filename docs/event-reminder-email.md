# Event reminder and announcement emails

`POST /api/v1/events/{event_id}/remind` (event managers) emails everyone whose registration is `confirmed` or `attended`. That includes online registrations and walk-ins; both are stored the same way, with their own email address. Cancelled and no-show registrations are not reminded. The reminder also goes out as an in-app / push notification to people the platform can match to an account.

## What the answer means

The call returns 201 as before, but the body now says what happened:

```json
{
  "message": "Reminder emailed to 9 of 12 registered people; 3 could not be reached.",
  "event_id": "…",
  "total": 12, "emailed": 9, "failed": 2, "skipped": 1,
  "by_source": {
    "online":  { "total": 9, "emailed": 8, "failed": 1, "skipped": 0 },
    "walk_in": { "total": 3, "emailed": 1, "failed": 1, "skipped": 1 }
  },
  "failures": [ { "email": "a@example.com", "source": "walk_in", "reason": "The mail server refused this recipient address." } ],
  "smtp_configured": true
}
```

- `emailed`: the mail server accepted the message. It cannot tell whether the person saw it, so ask them to check their spam folder.
- `failed`: the mail server did not accept it. `failures[].reason` says why.
- `skipped`: the registration has no valid email address.
- If `emailed` is 0 and `total` is more than 0, nothing reached anyone. The `message` carries the first reason.

## If nothing is delivered

1. Look at `failures[].reason` and `smtp_configured` in the answer.
2. As a Platform Super Admin, call `GET /api/v1/admin/notifications/email-diagnostics`. It tries the mail server step by step (connect, STARTTLS, sign in) and names the step that fails. Add `?send_to=you@example.com` to also send one test email. Enterprise Admins cannot call it.
3. Typical causes, from the reasons above:
   - `email_user` / `email_pass` are empty on the server.
   - The sign-in is rejected. Gmail needs an app password, not the account password.
   - The server cannot reach `SMTP_HOST:SMTP_PORT` (default `smtp.gmail.com:587`). Many hosts block outbound SMTP.
   - The message was accepted but lands in spam. Use a sending domain with SPF and DKIM set up.

Settings: `email_user`, `email_pass`, `SMTP_HOST`, `SMTP_PORT`. Every reminder uses one connection and one sign-in for the whole list, and each connection has a 20 second timeout.


## Announcements

`POST /api/v1/events/{event_id}/announcements` (event managers) sends the message to everyone with a `confirmed` or `attended` registration, online and walk-in alike.

**Channels.** If the request does not list `channels`, the announcement goes in the app **and by email**. Before, an unlisted `channels` silently meant in-app only, so no email was ever tried. Only the channels you list are used when you do list them, so `["in_app"]` sends no email, and the answer says so (`email_requested: false`). Walk-in attendees usually have no app account, so email is how they are reached.

The answer keeps its old fields and now also says what happened, in the same shape as the reminder:

```json
{
  "recipient_count": 12, "sent_count": 9,
  "delivery_summary": "Emailed 9 of 12 registered people; 3 could not be reached.",
  "channels": ["in_app", "email"], "email_requested": true,
  "emailed": 9, "failed": 2, "skipped": 1,
  "by_source": { "online": { "total": 9, "emailed": 8, "failed": 1, "skipped": 0 }, "walk_in": { "total": 3, "emailed": 1, "failed": 1, "skipped": 1 } },
  "failures": [ { "email": "a@example.com", "source": "walk_in", "reason": "The mail server refused this recipient address." } ],
  "smtp_configured": true
}
```

`sent_count` is the number of people reached by at least one channel: an email the mail server accepted, or an app account found. It used to count everyone whose send did not raise an error, which was always every recipient, even when every email failed.

If emails fail, use `GET /api/v1/admin/notifications/email-diagnostics` as described above.
