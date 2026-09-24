# Socket.IO using the existing Web Auth session

## Browser contract

Use the Enterprise site's own origin when its BFF proxies Socket.IO:

```javascript
const socket = io(window.location.origin, {
  path: "/api/socket.io",
  withCredentials: true
});
```

No `auth.token`, browser-readable access JWT, or chat-token request is needed.
The path must match the configured/proxied `SOCKETIO_PATH`.

## Server-side integration

Two session representations are supported through distinct integrations:

1. **Cookie contains the access JWT:** forward the Cookie header to Chat.
   Socket.IO already reads `WEB_SESSION_COOKIE_NAME` (default `access_token`)
   and `WEB_SESSION_COOKIE_FALLBACK_NAMES` (default `he_session`). The server
   validates the token and derives the authenticated user using the existing
   REST chat JWT resolver. Cookie names alone do not establish authentication.
2. **Cookie is an opaque/encrypted Web Auth session:** the Enterprise BFF must
   load/decrypt its existing session and obtain the access token, exactly as it
   does for REST. Forward `Authorization: Bearer <access_token>` to the Chat
   handshake server-side. Chat now accepts this header for Socket.IO too.
   Chat does not own the Enterprise session store or decrypt opaque cookies.

The BFF must proxy both Engine.IO polling GET/POST requests and WebSocket
upgrades. A normal JSON API handler is not enough. Preserve the request path,
query string and transport session ID; inject the Bearer header when establishing
the upstream transport. Derive it only from the authenticated BFF session, never
from a browser-supplied user/provider ID. Overwrite any forwarded Authorization
header with the token resolved from that session.

The BFF implementation is outside this repository; its proxy/session wiring has
not been changed or verified here. Existing `auth.token` and legacy query-token
clients remain compatible, but the browser integration above does not use them.
Credential precedence is forwarded Bearer header, explicit auth payload, session
cookie, then legacy query token. Invalid selected credentials fail authentication
rather than falling back to a different user or development identity.

## Domains and origins

HttpOnly prevents JavaScript from reading the cookie, not the browser from
sending it. Domain, Path, Secure and SameSite rules still apply. A cookie owned
by the Enterprise origin cannot be sent to an unrelated Chat domain merely by
setting `withCredentials: true`. Prefer the same-origin BFF proxy so the existing
cookie scope can remain unchanged.

If connecting directly to a cookie-compatible Chat origin, use
`io(chatOrigin, {path: "/api/socket.io", withCredentials: true})` and configure
the exact Enterprise origin in `CORS_ORIGINS`/`FRONTEND_URL`. Socket.IO sends
credentialed CORS responses and Engine.IO checks Origin. Empty allowed origins
now retain Engine.IO's same-origin check, rather than disabling origin checks.
Do not configure a wildcard for cookie authentication. The proxy must preserve
the browser Origin, or validate it itself before forwarding.

References: [Socket.IO client options](https://socket.io/docs/v4/client-options/),
[MDN cookie scope and HttpOnly](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie).

## Cross-domain fallback

The repository already has `POST /api/v1/auth/chat-token`, authenticated by the
existing session cookie or a BFF-forwarded login Bearer token. It issues a derived
chat credential with a default lifetime of 300 seconds. It is **chat-scoped,
not socket-only**: it also permits REST chat APIs. No new token endpoint was
added by this change and no socket-only guarantee is claimed for that endpoint.

If the BFF cannot proxy persistent transports and direct cookie delivery is
impossible, a separately scoped socket-only credential would require an explicit
issuance/validation contract. It can derive from the same Web Auth session; it
does not require a second login system. Cookie/BFF integration should be resolved
first using the actual Enterprise origin and session-cookie representation.

## Authentication lifecycle and tests

Missing, invalid and expired credentials are rejected at namespace connect.
The existing development identity is available only for a missing credential
when `ENABLE_DEV_TOKEN` is explicitly enabled outside production. Membership
checks on conversation operations are unchanged.

Authentication is checked on connect/reconnect. An already-open connection is
not automatically revoked when the Web Auth cookie changes or the user logs out.
The browser/BFF must disconnect on logout and reconnect through the refreshed
session; centralized live-session revocation is not implemented by this change.

`tests/test_socket_session_auth.py` exercises real Engine.IO/Socket.IO handshakes
without JavaScript tokens, fallback cookie names, BFF Bearer forwarding, invalid
and expired sessions, credentialed polling CORS and untrusted origins. External
BFF integration and browser-specific cross-site cookie policies need deployment
testing with the actual origin/cookie configuration.

Verification: 28 tests passed across `test_socket_session_auth.py`,
`test_socket.py`, `test_socket_app.py`, and `test_chat_token.py`. Python
compilation and Git whitespace checks passed. Changes are local, not deployed.
