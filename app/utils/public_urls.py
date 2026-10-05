"""HTTPS-only links for everything the API hands to clients.

Mobile (Android/iOS) blocks cleartext ``http://`` media. Training media URLs are stored as absolute
URLs built from ``PUBLIC_API_BASE_URL`` at upload time, so rows saved while that was
``http://13.207.85.164`` still carry the bare-IP http link. These helpers fix both sides:

* write time  - ``save_training_upload`` and Training create/update normalize what they store;
* read time   - ``HttpsLinkRewriteMiddleware`` rewrites Training API JSON, which also repairs rows
  that were saved long ago.

Rules for a value that is *entirely* an ``http://`` URL (embedded text is never touched):

* a bare-IP host serving one of our own files (path under ``/api/``) -> the configured canonical
  HTTPS origin (``PUBLIC_MEDIA_BASE_URL`` / ``PUBLIC_API_BASE_URL``). Without a canonical hostname the
  value is left alone rather than turned into ``https://<ip>`` (which could never present a valid
  certificate);
* any other host -> same host, ``https``, with a redundant ``:80`` dropped;
* ``localhost`` and non-http values (https, relative, mailto, data) -> unchanged.
"""
import ipaddress
from urllib.parse import SplitResult, urlsplit, urlunsplit

from app.core.config import settings

OWN_FILE_PATH_PREFIX = "/api/"


def _is_ip(host: str | None) -> bool:
    if not host:
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def canonical_https_origin() -> SplitResult | None:
    """The public https origin (scheme + host) links should use, or None if none is configured."""
    for candidate in (settings.PUBLIC_MEDIA_BASE_URL, settings.PUBLIC_API_BASE_URL):
        parts = urlsplit((candidate or "").strip())
        host = parts.hostname
        if not host or _is_ip(host) or host == "localhost":
            continue
        return SplitResult("https", parts.netloc, "", "", "")
    return None


def public_media_base() -> str:
    """Origin to prefix on newly generated file URLs (no trailing slash; empty = relative URLs)."""
    if settings.force_https_media_urls:
        canonical = canonical_https_origin()
        if canonical is not None:
            return f"{canonical.scheme}://{canonical.netloc}"
    return (settings.PUBLIC_API_BASE_URL or "").strip().rstrip("/")


def _is_whole_http_url(value: str) -> bool:
    return value[:7].lower() == "http://" and not any(ch.isspace() for ch in value)


def to_https(value, *, enabled: bool | None = None):
    """Return ``value`` as an https link per the module rules; non-strings pass through untouched."""
    if enabled is None:
        enabled = settings.force_https_media_urls
    if not enabled or not isinstance(value, str) or not _is_whole_http_url(value):
        return value

    parts = urlsplit(value)
    host = parts.hostname
    if not host or host == "localhost":
        return value

    if _is_ip(host):
        canonical = canonical_https_origin()
        if canonical is None or not parts.path.startswith(OWN_FILE_PATH_PREFIX):
            return value
        return urlunsplit(("https", canonical.netloc, parts.path, parts.query, parts.fragment))

    netloc = parts.netloc[:-3] if parts.netloc.endswith(":80") else parts.netloc
    return urlunsplit(("https", netloc, parts.path, parts.query, parts.fragment))


def deep_https(obj, *, enabled: bool | None = None) -> bool:
    """Rewrite every whole-string http URL inside parsed JSON (dicts/lists) in place.
    Returns True when anything changed."""
    if enabled is None:
        enabled = settings.force_https_media_urls
    if not enabled:
        return False
    return _walk(obj)


def _walk(node) -> bool:
    changed = False
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                new = to_https(value, enabled=True)
                if new != value:
                    node[key] = new
                    changed = True
            elif isinstance(value, (dict, list)):
                changed = _walk(value) or changed
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str):
                new = to_https(value, enabled=True)
                if new != value:
                    node[index] = new
                    changed = True
            elif isinstance(value, (dict, list)):
                changed = _walk(value) or changed
    return changed
