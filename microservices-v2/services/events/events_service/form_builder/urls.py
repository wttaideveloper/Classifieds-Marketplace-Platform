"""Reading a URL: is it safe, what is it, and its normal form.

    inspect_url("https://youtu.be/dQw4w9WgXcQ?t=42")
    -> {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "provider": "youtube", "video_id": "dQw4w9WgXcQ",
        "thumbnail_url": "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg", "host": "www.youtube.com"}

Providers: `youtube`, `vimeo`, `external` (any other http(s) address). Vimeo has no thumbnail without calling Vimeo's oEmbed service, so
`thumbnail_url` is null for it: nothing here makes a network call. Unsafe addresses raise `UnsafeUrl` with a reason the user can read.
"""
import ipaddress
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

MAX_URL = 2000
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}
VIMEO_HOSTS = {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}
BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")


class UnsafeUrl(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _host_is_unsafe(host: str) -> str | None:
    if host == "localhost" or host.endswith(BLOCKED_SUFFIXES):
        return "Addresses on this computer or a private network are not allowed"
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if "." not in host:
            return "The address needs a full domain name"
        return None
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return "Addresses on this computer or a private network are not allowed"
    return None


def _youtube_id(host: str, parts, query: dict) -> str | None:
    segments = [s for s in parts.path.split("/") if s]
    candidate = None
    if host == "youtu.be":
        candidate = segments[0] if segments else None
    elif segments and segments[0] in ("embed", "shorts", "live", "v") and len(segments) > 1:
        candidate = segments[1]
    elif segments == ["watch"] or (segments and segments[0] == "watch"):
        candidate = (query.get("v") or [None])[0]
    return candidate if candidate and YOUTUBE_ID.match(candidate) else None


def _vimeo_id(parts) -> str | None:
    for segment in reversed([s for s in parts.path.split("/") if s]):
        if segment.isdigit() and len(segment) <= 12:
            return segment
    return None


def inspect_url(raw, *, allow_relative: bool = False) -> dict:
    """Normalize and classify an address, or raise UnsafeUrl. `allow_relative` accepts a path on this site such as /api/v1/... (uploaded files)."""
    url = str(raw or "").strip()
    if not url:
        raise UnsafeUrl("EMPTY", "Enter a web address")
    if len(url) > MAX_URL:
        raise UnsafeUrl("TOO_LONG", f"The address can have at most {MAX_URL} characters")
    if any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise UnsafeUrl("INVALID", "The address cannot contain spaces or control characters")
    if allow_relative and url.startswith("/") and not url.startswith("//"):
        return {"url": url, "provider": "internal", "video_id": None, "thumbnail_url": None, "host": None}
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise UnsafeUrl("UNSAFE_SCHEME", "Only http:// and https:// addresses are allowed")
    if not parts.hostname:
        raise UnsafeUrl("INVALID", "The address has no website name")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrl("CREDENTIALS", "An address with a user name or password in it is not allowed")
    host = parts.hostname.lower().rstrip(".")
    reason = _host_is_unsafe(host)
    if reason:
        raise UnsafeUrl("PRIVATE_HOST", reason)
    try:
        port = parts.port
    except ValueError:
        raise UnsafeUrl("INVALID", "The port number is not valid")
    query = parse_qs(parts.query)
    if host in YOUTUBE_HOSTS or host == "youtu.be":
        video_id = _youtube_id(host, parts, query)
        if video_id:
            return {"url": f"https://www.youtube.com/watch?v={video_id}", "provider": "youtube", "video_id": video_id,
                    "thumbnail_url": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg", "host": "www.youtube.com"}
        raise UnsafeUrl("INVALID_VIDEO", "This YouTube address does not point to a video")
    if host in VIMEO_HOSTS:
        video_id = _vimeo_id(parts)
        if video_id:
            return {"url": f"https://vimeo.com/{video_id}", "provider": "vimeo", "video_id": video_id, "thumbnail_url": None, "host": "vimeo.com"}
        raise UnsafeUrl("INVALID_VIDEO", "This Vimeo address does not point to a video")
    netloc = host if port in (None, 80 if parts.scheme.lower() == "http" else 443) else f"{host}:{port}"
    normal = urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))
    return {"url": normal, "provider": "external", "video_id": None, "thumbnail_url": None, "host": host}
