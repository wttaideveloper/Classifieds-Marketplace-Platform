"""A small allow-list cleaner for rich text that other people will read (blog posts, eBook pages).

Only the tags a rich-text editor produces are kept; every other tag is removed but its text stays, and script / style / iframe-like
elements are removed with their content. Scripts, event handlers, javascript: links and CSS that can load things never survive.

Two profiles:
  basic  headings, bold, italic, lists, links, images, quotes, code  (blog posts)
  ebook  the same without images (an eBook's pictures are separate blocks) plus tables, sub/sup, highlight, a few classes, safe
         inline styles (alignment, colour, weight) and `data-image-id` placeholders that say where one of the page's images goes

`clean_html_report` also says what was thrown away, so an editor that keeps producing unsafe markup can be told.
"""
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

BASIC_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li", "blockquote", "code", "pre",
    "a", "img", "span", "div", "figure", "figcaption",
}
EBOOK_TAGS = (BASIC_TAGS - {"img"}) | {
    "h5", "h6", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "sub", "sup", "mark", "small", "del", "ins", "dl", "dt", "dd",
}
READER_TAGS = {"p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "strong", "b", "em", "i"}
VOID_TAGS = {"br", "hr", "img"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math", "form", "textarea", "select", "video", "audio", "link", "meta", "base"}
BASIC_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title", "width", "height"}}
EBOOK_ATTRS = {"a": {"href", "title"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan", "scope"}}
LINK_SCHEMES = {"http", "https", "mailto", "tel"}
IMAGE_SCHEMES = {"http", "https"}

_CLASS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,40}( [A-Za-z][A-Za-z0-9_-]{0,40}){0,5}$")
_PLACEHOLDER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_INT_RE = re.compile(r"^\d{1,2}$")
_COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\(\s*\d{1,3}\s*(,\s*[\d.]{1,5}\s*){2,3}\)|[a-zA-Z]{3,20})$")
_SIZE_RE = re.compile(r"^\d{1,3}(\.\d{1,2})?(px|em|rem|%)$")
SAFE_STYLES = {
    "text-align": re.compile(r"^(left|right|center|justify)$"),
    "color": _COLOR_RE,
    "background-color": _COLOR_RE,
    "font-weight": re.compile(r"^(normal|bold|[1-9]00)$"),
    "font-style": re.compile(r"^(normal|italic)$"),
    "text-decoration": re.compile(r"^(none|underline|line-through)$"),
    "font-size": _SIZE_RE,
}


def _safe_url(value: str | None, schemes: set[str], allow_anchor: bool = False) -> str | None:
    url = (value or "").strip()
    if not url:
        return None
    compact = "".join(ch for ch in url if ch.isprintable() and not ch.isspace()).lower()
    parsed = urlparse(compact)
    if parsed.scheme:
        return url if parsed.scheme in schemes else None
    if compact.startswith("//"):
        return None                       # "//host" means "any scheme"
    if compact.startswith("#") and not allow_anchor:
        return None
    return url


def safe_style(value: str) -> str | None:
    """Only the declarations in SAFE_STYLES with plain values; anything else (url(), expression, position, ...) is dropped."""
    kept = []
    for declaration in (value or "").split(";"):
        if ":" not in declaration:
            continue
        name, _, val = declaration.partition(":")
        name, val = name.strip().lower(), val.strip().lower()
        rule = SAFE_STYLES.get(name)
        if rule is not None and rule.match(val):
            kept.append(f"{name}: {val}")
    return "; ".join(kept) or None


class _Cleaner(HTMLParser):
    def __init__(self, profile: str = "basic") -> None:
        super().__init__(convert_charrefs=True)
        self.profile = profile
        self.tags = READER_TAGS if profile == "ebook_reader" else EBOOK_TAGS if profile == "ebook" else BASIC_TAGS
        self.attrs = {} if profile == "ebook_reader" else EBOOK_ATTRS if profile == "ebook" else BASIC_ATTRS
        self.out: list[str] = []
        self.open_tags: list[str] = []
        self.skip_depth = 0
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.note(f"removed <{tag}>")
            if tag not in VOID_TAGS:
                self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag not in self.tags:
            if tag == "img" and self.profile == "ebook":
                self.note("removed <img> (add pictures as image blocks)")
            return
        kept = []
        for name, value in attrs:
            if name.startswith("on"):
                self.note("removed an event handler")
                continue
            if name in self.attrs.get(tag, ()):
                if name == "href":
                    cleaned = _safe_url(value, LINK_SCHEMES, allow_anchor=self.profile == "ebook")
                    if cleaned is None and value:
                        self.note("removed an unsafe link")
                    value = cleaned
                elif name == "src":
                    cleaned = _safe_url(value, IMAGE_SCHEMES)
                    if cleaned is None and value:
                        self.note("removed an unsafe image address")
                    value = cleaned
                elif name in ("colspan", "rowspan"):
                    value = value if value and _INT_RE.match(value) else None
                elif name == "scope":
                    value = value if value in ("row", "col", "rowgroup", "colgroup") else None
                if value is None:
                    continue
                kept.append(f' {name}="{escape(value, quote=True)}"')
            elif self.profile == "ebook":
                if name == "style":
                    cleaned = safe_style(value or "")
                    if cleaned:
                        kept.append(f' style="{escape(cleaned, quote=True)}"')
                    elif value:
                        self.note("removed unsupported styling")
                elif name == "class" and value and _CLASS_RE.match(value.strip()):
                    kept.append(f' class="{escape(value.strip(), quote=True)}"')
                elif name == "data-image-id" and tag in ("div", "figure", "p") and value and _PLACEHOLDER_RE.match(value):
                    kept.append(f' data-image-id="{escape(value, quote=True)}"')
        if tag == "img" and not any(a.startswith(" src=") for a in kept):
            return
        if tag == "a":
            kept.append(' rel="noopener noreferrer nofollow" target="_blank"')
        self.out.append(f"<{tag}{''.join(kept)}>")
        if tag not in VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in VOID_TAGS:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth or tag not in self.tags or tag in VOID_TAGS or tag not in self.open_tags:
            return
        while self.open_tags:       # close anything left open inside it, so the markup stays balanced
            last = self.open_tags.pop()
            self.out.append(f"</{last}>")
            if last == tag:
                break

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(escape(data, quote=False))

    def close_all(self) -> str:
        while self.open_tags:
            self.out.append(f"</{self.open_tags.pop()}>")
        return "".join(self.out)


def clean_html_report(value: str | None, profile: str = "basic") -> tuple[str, list[str]]:
    """(the cleaned text, what was thrown away)."""
    cleaner = _Cleaner(profile)
    cleaner.feed(value or "")
    cleaner.close()
    return cleaner.close_all(), cleaner.notes


def clean_html(value: str | None, profile: str = "basic") -> str:
    """The text with only the allowed formatting left. Plain text passes through unchanged (apart from escaping)."""
    return clean_html_report(value, profile)[0]
