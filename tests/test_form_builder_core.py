"""The shared form-builder core: the 13 field types, URL inspection, builder operations, protected fields and the three projections."""
from datetime import date, timedelta

import pytest

from app.form_builder import operations as ops
from app.form_builder import projection
from app.form_builder.fields import TYPES, normalize_field, validate_value
from app.form_builder.urls import UnsafeUrl, inspect_url

OPTIONS = [{"value": "kids", "label": "Kids"}, {"value": "adults", "label": "Adults"}]


def field(type_, key="f_one", **over):
    errors: list = []
    raw = {"key": key, "label": over.pop("label", "Field one"), "type": type_, **over}
    if type_ in ("dropdown", "radio", "checkbox", "select", "multiselect") and "options" not in raw:
        raw["options"] = OPTIONS
    built = normalize_field(raw, path="field", errors=errors)
    assert errors == [] and built is not None, errors
    return built


def check(f, value):
    errors: list = []
    cleaned = validate_value(f, value, "v", errors)
    return cleaned, [e["code"] for e in errors]


# --- the types ---------------------------------------------------------------------------------------------------------------

def test_there_are_fourteen_explicit_types_each_with_its_own_renderer():
    assert {t for t in TYPES if not TYPES[t].get("module")} == {"short_text", "rich_text", "long_text", "dropdown", "radio", "checkbox", "number", "date", "time", "reference_url",
                          "additional_images", "supporting_document", "video", "tags"}
    built = {t: field(t) for t in TYPES if not TYPES[t].get("module")}
    assert {t: f["renderer"] for t, f in built.items()} == {
        "short_text": "text_input", "rich_text": "rich_text_editor", "long_text": "textarea", "dropdown": "select", "radio": "radio_group", "checkbox": "checkbox_group",
        "number": "number_input", "date": "date_picker", "time": "time_picker", "reference_url": "url_input", "additional_images": "image_uploader",
        "supporting_document": "document_uploader", "video": "video_input", "tags": "tag_input"}
    f = built["short_text"]
    assert {"id", "key", "label", "type", "renderer", "placeholder", "help_text", "required", "enabled", "order", "validation", "options", "visibility", "system"} <= set(f)
    assert f["visibility"] == "published" and f["enabled"] is True and f["required"] is False and f["system"] is False


def test_older_names_become_explicit_types_and_are_never_returned():
    for old, new in (("text", "short_text"), ("textarea", "long_text"), ("select", "dropdown"), ("multiselect", "checkbox"), ("url", "reference_url")):
        assert field(old)["type"] == new
    legacy = field("short_text", visible=False, visibility="internal")
    assert legacy["enabled"] is False and legacy["visibility"] == "enterprise_admin_only" and legacy["required"] is False
    assert field("short_text", enterprise_only=True)["visibility"] == "enterprise_admin_only"


def test_a_field_definition_is_checked_with_every_problem_reported():
    errors: list = []
    assert normalize_field({"key": "Bad Key", "label": "x", "type": "short_text"}, path="f", errors=errors) is None
    assert normalize_field({"key": "ok_key", "label": "", "type": "short_text"}, path="f", errors=errors) is None
    assert normalize_field({"key": "ok_key", "label": "x", "type": "colour_picker"}, path="f", errors=errors) is None
    normalize_field({"key": "pick", "label": "Pick", "type": "dropdown"}, path="f", errors=errors)
    normalize_field({"key": "one", "label": "One", "type": "short_text", "options": [{"value": "a"}]}, path="f", errors=errors)
    normalize_field({"key": "rr", "label": "R", "type": "radio", "options": [{"value": "only"}]}, path="f", errors=errors)
    normalize_field({"key": "vv", "label": "V", "type": "short_text", "validation": {"max_length": 5000, "bogus": 1, "min_length": -1}}, path="f", errors=errors)
    normalize_field({"key": "nn", "label": "N", "type": "number", "validation": {"min": 10, "max": 1, "decimals": 9}}, path="f", errors=errors)
    normalize_field({"key": "dd", "label": "D", "type": "date", "validation": {"min": "soon", "not_in_past": True, "not_in_future": True}}, path="f", errors=errors)
    normalize_field({"key": "tt", "label": "T", "type": "time", "validation": {"min": "25:00"}}, path="f", errors=errors)
    normalize_field({"key": "pp", "label": "P", "type": "reference_url", "validation": {"allowed_providers": ["tiktok"]}}, path="f", errors=errors)
    normalize_field({"key": "ii", "label": "I", "type": "additional_images", "validation": {"allowed_types": ["exe"], "max_file_size_mb": 500}}, path="f", errors=errors)
    normalize_field({"key": "ss", "label": "S", "type": "short_text", "visibility": "everyone"}, path="f", errors=errors)
    codes = {e["code"] for e in errors}
    assert {"INVALID_KEY", "REQUIRED", "INVALID_TYPE", "OPTIONS_NOT_ALLOWED", "UNKNOWN_RULE", "INVALID_RULE", "INVALID"} <= codes
    paths = {e["field"] for e in errors}
    assert "f.validation.bogus" in paths and "f.validation.decimals" in paths and "f.validation.allowed_types" in paths and "f.options" in paths


def test_duplicate_options_are_refused_by_value_and_by_label():
    errors: list = []
    normalize_field({"key": "pick", "label": "Pick", "type": "dropdown", "options": [{"value": "a", "label": "A"}, {"value": "A", "label": "Other"}]}, path="f", errors=errors)
    normalize_field({"key": "pick2", "label": "Pick", "type": "checkbox", "options": [{"value": "a", "label": "Same"}, {"value": "b", "label": "same"}]}, path="f", errors=errors)
    assert [e["code"] for e in errors] == ["DUPLICATE_OPTION", "DUPLICATE_OPTION"]


# --- values ------------------------------------------------------------------------------------------------------------------

def test_short_long_and_rich_text_rules():
    short = field("short_text", validation={"min_length": 3, "max_length": 8})
    assert check(short, " Hello ") == ("Hello", []) and check(short, "Hi")[1] == ["TOO_SHORT"] and check(short, "x" * 9)[1] == ["TOO_LONG"]
    assert check(short, 12345) == ("12345", []) and check(short, ["a"])[1] == ["INVALID_TYPE"]
    assert check(field("short_text", validation={"format": "email"}), "no-at-sign")[1] == ["INVALID_FORMAT"]
    assert check(field("short_text", validation={"format": "email"}), "a@b.co") == ("a@b.co", [])
    assert check(field("short_text", validation={"format": "phone"}), "+91 98765 43210")[1] == [] and check(field("short_text", validation={"format": "phone"}), "abc")[1] == ["INVALID_FORMAT"]
    assert check(field("short_text", validation={"pattern": r"^[A-Z]{3}\d+$"}), "abc1")[1] == ["INVALID_FORMAT"]
    assert check(field("short_text"), "x" * 256)[1] == ["TOO_LONG"]                                                # the default limit
    long_ = field("long_text", validation={"min_length": 10})
    assert check(long_, "short")[1] == ["TOO_SHORT"] and check(long_, "a long enough text")[1] == [] and check(field("long_text"), "x" * 5001)[1] == ["TOO_LONG"]
    rich = field("rich_text", validation={"min_length": 5, "max_length": 20})
    assert check(rich, "<p><b>Hi</b></p>")[1] == ["TOO_SHORT"] and check(rich, "<p>" + "x" * 30 + "</p>")[1] == ["TOO_LONG"]
    cleaned, codes = validate_value(rich, "<p>Fine text</p><script>x()</script>", "v", errs := [], sanitize_html=lambda h: h.replace("<script>x()</script>", "")), errs
    assert cleaned == "<p>Fine text</p>" and errs == []


def test_choice_fields():
    for kind in ("dropdown", "radio"):
        f = field(kind)
        assert check(f, "kids") == ("kids", []) and check(f, "pets")[1] == ["INVALID_OPTION"]
    box = field("checkbox", validation={"min_items": 1, "max_items": 2})
    assert check(box, ["kids", "adults", "kids"]) == (["kids", "adults"], []) and check(box, [])[1] == ["TOO_FEW"] and check(box, ["pets"])[1] == ["INVALID_OPTION"]
    assert check(box, "kids")[1] == ["INVALID_OPTION"]
    wide = field("checkbox", options=[{"value": str(i), "label": f"L{i}"} for i in range(4)], validation={"max_items": 2})
    assert check(wide, ["0", "1", "2"])[1] == ["TOO_MANY"]


def test_number_rules_including_decimals():
    n = field("number", validation={"min": 0, "max": 100, "decimals": 2})
    assert check(n, 5) == (5, []) and check(n, "12.50") == (12.5, []) and check(n, 12.345)[1] == ["TOO_MANY_DECIMALS"]
    assert check(n, -1)[1] == ["TOO_SMALL"] and check(n, 101)[1] == ["TOO_LARGE"] and check(n, "abc")[1] == ["INVALID_TYPE"] and check(n, True)[1] == ["INVALID_TYPE"]
    whole = field("number", validation={"decimals": 0})
    assert check(whole, 7.0) == (7, []) and check(whole, 7.5)[1] == ["TOO_MANY_DECIMALS"] and check(whole, "1e3") == (1000, [])
    assert check(n, "nan")[1] == ["INVALID_TYPE"]


def test_date_and_time_rules():
    d = field("date", validation={"min": "2026-01-01", "max": "2026-12-31"})
    assert check(d, "2026-06-15") == ("2026-06-15", []) and check(d, "2025-12-31")[1] == ["TOO_SMALL"] and check(d, "2027-01-01")[1] == ["TOO_LARGE"] and check(d, "15/06/2026")[1] == ["INVALID_FORMAT"]
    today = date.today()
    assert check(field("date", validation={"not_in_past": True}), (today - timedelta(days=1)).isoformat())[1] == ["IN_THE_PAST"]
    assert check(field("date", validation={"not_in_past": True}), today.isoformat())[1] == []
    assert check(field("date", validation={"not_in_future": True}), (today + timedelta(days=1)).isoformat())[1] == ["IN_THE_FUTURE"]
    t = field("time", validation={"min": "09:00", "max": "17:30"})
    assert check(t, "10:15") == ("10:15", []) and check(t, "09:00:30") == ("09:00", []) and check(t, "08:59")[1] == ["TOO_SMALL"] and check(t, "17:31")[1] == ["TOO_LARGE"]
    assert check(t, "25:00")[1] == ["INVALID_FORMAT"] and check(t, "9am")[1] == ["INVALID_FORMAT"]


def test_reference_url_field_normalizes_and_restricts():
    f = field("reference_url")
    value, codes = check(f, "https://EXAMPLE.com:443/Path?a=1#frag")
    assert codes == [] and value == {"url": "https://example.com/Path?a=1", "provider": "external", "video_id": None, "thumbnail_url": None}
    yt, _ = check(f, "https://youtu.be/dQw4w9WgXcQ")
    assert yt["provider"] == "youtube" and yt["video_id"] == "dQw4w9WgXcQ" and yt["thumbnail_url"].endswith("/dQw4w9WgXcQ/hqdefault.jpg")
    assert check(f, "javascript:alert(1)")[1] == ["UNSAFE_SCHEME"] and check(f, "http://localhost/x")[1] == ["PRIVATE_HOST"]
    assert check(field("reference_url", validation={"require_https": True}), "http://example.com")[1] == ["HTTPS_REQUIRED"]
    only_video = field("reference_url", validation={"allowed_providers": ["youtube", "vimeo"]})
    assert check(only_video, "https://example.com/page")[1] == ["PROVIDER_NOT_ALLOWED"] and check(only_video, "https://vimeo.com/76979871")[1] == []


def test_media_fields_check_counts_types_sizes_and_links():
    images = field("additional_images", validation={"min_items": 1, "max_items": 2, "max_file_size_mb": 1, "allowed_types": ["png", "jpg"]})
    ok, codes = check(images, [{"url": "/api/v1/cms/blogs/files/abc", "name": "a.png", "size": 1000, "content_type": "image/png", "alt": "A"}, "https://cdn.example.com/b.jpg"])
    assert codes == [] and ok[0] == {"url": "/api/v1/cms/blogs/files/abc", "name": "a.png", "alt": "A", "size": 1000, "content_type": "image/png"} and ok[1] == {"url": "https://cdn.example.com/b.jpg"}
    assert check(images, [])[1] == ["TOO_FEW"] and check(images, ["https://a.com/1.png", "https://a.com/2.png", "https://a.com/3.png"])[1] == ["TOO_MANY"]
    assert check(images, [{"url": "https://a.com/x.png", "size": 2 * 1024 * 1024}])[1] == ["FILE_TOO_LARGE"]
    assert check(images, [{"url": "https://a.com/x.webp"}])[1] == ["FILE_TYPE_NOT_ALLOWED"] and check(images, [{"url": "https://a.com/x", "content_type": "image/gif"}])[1] == ["FILE_TYPE_NOT_ALLOWED"]
    assert check(images, ["https://a.com/x.png", "https://a.com/x.png"])[1] == ["DUPLICATE_ITEM"] and check(images, ["file:///etc/passwd"])[1] == ["UNSAFE_SCHEME"]
    assert check(images, [{"nourl": 1}])[1] == ["INVALID_FORMAT"]

    docs = field("supporting_document")
    assert check(docs, [{"url": "/api/v1/x/1", "name": "Report.PDF", "content_type": "application/pdf", "size": 10}])[1] == []
    assert check(docs, [{"url": "/api/v1/x/2", "name": "run.exe"}])[1] == ["FILE_TYPE_NOT_ALLOWED"] and check(docs, [{"url": "https://a.com/f.pdf", "size": 51 * 1024 * 1024}])[1] == ["FILE_TOO_LARGE"]


def test_video_field_takes_links_and_optionally_uploads():
    v = field("video", validation={"max_items": 2})
    items, codes = check(v, ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=9", {"url": "https://vimeo.com/channels/staffpicks/76979871"}])
    assert codes == [] and [(i["provider"], i["video_id"]) for i in items] == [("youtube", "dQw4w9WgXcQ"), ("vimeo", "76979871")]
    assert items[0]["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ" and items[0]["thumbnail_url"].startswith("https://img.youtube.com/vi/")
    up, codes = check(v, [{"url": "/api/v1/cms/blogs/files/9", "name": "clip.mp4", "content_type": "video/mp4", "size": 5000}])
    assert codes == [] and up[0]["provider"] == "upload"
    assert check(v, [{"url": "/api/v1/x", "name": "clip.avi"}])[1] == ["FILE_TYPE_NOT_ALLOWED"] and check(v, [{"url": "/f/1", "size": 600 * 1024 * 1024}])[1] == ["FILE_TOO_LARGE"]
    assert check(field("video", validation={"allow_upload": False}), [{"url": "/api/v1/x/clip.mp4"}])[1] == ["UPLOAD_NOT_ALLOWED"]
    assert check(field("video", validation={"allowed_providers": ["youtube"]}), ["https://vimeo.com/76979871"])[1] == ["PROVIDER_NOT_ALLOWED"]
    assert check(v, ["https://a.com/1", "https://a.com/2", "https://a.com/3"])[1] == ["TOO_MANY"]
    assert check(v, ["https://www.youtube.com/watch?v=short"])[1] == ["INVALID_VIDEO"]


# --- URLs --------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, provider, video_id, url", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("http://youtu.be/dQw4w9WgXcQ?si=abc", "youtube", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://youtube.com/shorts/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://vimeo.com/76979871", "vimeo", "76979871", "https://vimeo.com/76979871"),
    ("https://player.vimeo.com/video/76979871?h=abc", "vimeo", "76979871", "https://vimeo.com/76979871"),
    ("HTTPS://Example.COM", "external", None, "https://example.com/"),
    ("http://example.com:80/a", "external", None, "http://example.com/a"),
    ("https://example.com:8443/a?b=1", "external", None, "https://example.com:8443/a?b=1"),
])
def test_urls_are_classified_and_normalized(raw, provider, video_id, url):
    info = inspect_url(raw)
    assert (info["provider"], info["video_id"], info["url"]) == (provider, video_id, url)
    assert (info["thumbnail_url"] is not None) == (provider == "youtube")


@pytest.mark.parametrize("raw, code", [
    ("", "EMPTY"), ("javascript:alert(1)", "UNSAFE_SCHEME"), ("data:text/html,<script>", "UNSAFE_SCHEME"), ("file:///etc/passwd", "UNSAFE_SCHEME"), ("ftp://example.com/x", "UNSAFE_SCHEME"),
    ("//evil.com/x", "UNSAFE_SCHEME"), ("https://user:pass@example.com/", "CREDENTIALS"), ("http://localhost:8000/", "PRIVATE_HOST"), ("http://127.0.0.1/", "PRIVATE_HOST"),
    ("http://10.0.0.5/admin", "PRIVATE_HOST"), ("http://192.168.1.1/", "PRIVATE_HOST"), ("http://169.254.169.254/latest/meta-data", "PRIVATE_HOST"), ("http://[::1]/", "PRIVATE_HOST"),
    ("http://printer.local/", "PRIVATE_HOST"), ("http://intranet/", "PRIVATE_HOST"), ("https://exa mple.com", "INVALID"), ("https://example.com/" + "a" * 2000, "TOO_LONG"),
    ("https://www.youtube.com/watch?v=short", "INVALID_VIDEO"), ("https://www.youtube.com/", "INVALID_VIDEO"), ("https://vimeo.com/about", "INVALID_VIDEO"), ("https://example.com:99999/", "INVALID"),
])
def test_unsafe_or_broken_urls_are_rejected_with_a_reason(raw, code):
    with pytest.raises(UnsafeUrl) as caught:
        inspect_url(raw)
    assert caught.value.code == code and caught.value.message


def test_relative_paths_are_only_for_uploaded_files():
    assert inspect_url("/api/v1/cms/blogs/files/9", allow_relative=True)["provider"] == "internal"
    with pytest.raises(UnsafeUrl):
        inspect_url("/api/v1/cms/blogs/files/9")
    with pytest.raises(UnsafeUrl):
        inspect_url("//evil.com", allow_relative=True)


# --- operations --------------------------------------------------------------------------------------------------------------

def base_form():
    d = {"sections": [ops.new_section_dict("Basics"), ops.new_section_dict("Details")]}
    for section_id, raw, system in ((0, {"key": "title", "label": "Title", "type": "short_text", "required": True}, True),
                                    (0, {"key": "summary", "label": "Summary", "type": "long_text"}, False),
                                    (1, {"key": "event_date", "label": "Event date", "type": "date"}, False)):
        d, _, _ = ops.add_field(d, d["sections"][section_id]["id"], raw, system=system)
    return d


def keys(d):
    return [[f["key"] for f in s["fields"]] for s in d["sections"]]


def test_fields_are_added_edited_duplicated_reordered_moved_and_deleted_without_touching_the_input():
    d0 = base_form()
    before = repr(d0)
    s1, s2 = d0["sections"][0]["id"], d0["sections"][1]["id"]

    d, added, text = ops.add_field(d0, s2, {"key": "speaker", "label": "Speaker", "type": "short_text", "help_text": "Full name"}, position=1)
    assert repr(d0) == before and keys(d) == [["title", "summary"], ["speaker", "event_date"]] and added["order"] == 1 and "speaker" in text
    assert [f["order"] for f in d["sections"][1]["fields"]] == [1, 2] and added["id"] and added["renderer"] == "text_input"

    d, edited, _ = ops.edit_field(d, "speaker", {"label": "Main speaker", "required": True, "placeholder": "e.g. Dr Rao", "validation": {"max_length": 60}})
    assert edited["label"] == "Main speaker" and edited["required"] is True and edited["validation"] == {"max_length": 60} and edited["id"] == added["id"] and edited["order"] == 1

    d, clone, _ = ops.duplicate_field(d, "speaker")
    assert clone["key"] == "speaker_copy" and clone["label"] == "Main speaker (copy)" and clone["id"] != added["id"] and keys(d)[1] == ["speaker", "speaker_copy", "event_date"]
    d, clone2, _ = ops.duplicate_field(d, "speaker")
    assert clone2["key"] == "speaker_copy2" and clone2["label"] != clone["label"]

    ids = [f["id"] for f in d["sections"][1]["fields"]]
    d, _ = ops.reorder_fields(d, s2, list(reversed(ids)))
    assert keys(d)[1] == ["event_date", "speaker_copy", "speaker_copy2", "speaker"] and [f["order"] for f in d["sections"][1]["fields"]] == [1, 2, 3, 4]
    with pytest.raises(ops.BuilderError) as bad:
        ops.reorder_fields(d, s2, ids[:2])
    assert bad.value.code == "INVALID_ORDER"

    d, moved, text = ops.move_field(d, "event_date", s1, position=2)
    assert keys(d)[0] == ["title", "event_date", "summary"] and "Moved" in text and moved["order"] == 2
    d, _ = ops.delete_field(d, "speaker_copy")
    assert "speaker_copy" not in sum(keys(d), [])


def test_duplicates_are_refused_and_keys_and_types_never_change():
    d = base_form()
    s1 = d["sections"][0]["id"]
    for raw, code in (({"key": "title", "label": "Another", "type": "short_text"}, "DUPLICATE_KEY"), ({"key": "other", "label": "summary", "type": "short_text"}, "DUPLICATE_LABEL")):
        with pytest.raises(ops.BuilderError) as e:
            ops.add_field(d, s1, raw)
        assert e.value.code == code
    d2, _, _ = ops.add_field(d, d["sections"][1]["id"], {"key": "other", "label": "Summary", "type": "short_text"})                # the same label in another section is fine
    assert "other" in sum(keys(d2), [])
    for patch, code in (({"key": "renamed"}, "VALIDATION_FAILED"), ({"type": "number"}, "VALIDATION_FAILED")):
        with pytest.raises(ops.BuilderError) as e:
            ops.edit_field(d, "summary", patch)
        assert e.value.errors[0]["code"] in ("KEY_IMMUTABLE", "TYPE_IMMUTABLE") and e.value.code == code
    with pytest.raises(ops.BuilderError) as e:
        ops.edit_field(d, "summary", {"label": "title"})                                                 # a label already used in the section
    assert e.value.code == "DUPLICATE_LABEL"
    with pytest.raises(ops.BuilderError) as e:
        ops.move_field(d, "event_date", s1) and ops.add_field(d, s1, {"key": "x_one", "label": "Title", "type": "short_text"})
    assert e.value.code == "DUPLICATE_LABEL"
    with pytest.raises(ops.BuilderError) as nf:
        ops.delete_field(d, "nope")
    assert nf.value.code == "FIELD_NOT_FOUND" and nf.value.status == 404
    with pytest.raises(ops.BuilderError) as many:
        big = d
        for i in range(ops.MAX_FIELDS):
            big, _, _ = ops.add_field(big, s1, {"key": f"f_{i:02d}", "label": f"F {i}", "type": "short_text"})
    assert many.value.code == "TOO_MANY_FIELDS"


def test_system_fields_are_protected():
    d = base_form()
    for call in (lambda: ops.delete_field(d, "title"), lambda: ops.edit_field(d, "title", {"enabled": False}), lambda: ops.edit_field(d, "title", {"required": False}),
                 lambda: ops.edit_field(d, "title", {"visibility": "enterprise_admin_only"})):
        with pytest.raises(ops.BuilderError) as e:
            call()
        assert e.value.code == "PROTECTED_FIELD" and e.value.status == 409
    ok, field_, _ = ops.edit_field(d, "title", {"label": "Headline", "help_text": "Short and clear"})
    assert field_["label"] == "Headline" and field_["system"] is True                                    # label and help text may change
    copy_, clone, _ = ops.duplicate_field(d, "title")
    assert clone["system"] is False                                                                      # a copy of a system field is an ordinary field
    ops.delete_field(copy_, clone["key"])
    with pytest.raises(ops.BuilderError) as e:
        ops.delete_section(d, d["sections"][0]["id"])
    assert e.value.code == "SECTION_HAS_PROTECTED_FIELDS"


def test_sections_are_added_edited_reordered_and_deleted():
    d = base_form()
    d, text = ops.add_section(d, title="Media", description="Pictures", position=1)
    assert [s["title"] for s in d["sections"]] == ["Media", "Basics", "Details"] and [s["order"] for s in d["sections"]] == [1, 2, 3] and "Media" in text
    with pytest.raises(ops.BuilderError) as dup:
        ops.add_section(d, title="media")
    assert dup.value.code == "DUPLICATE_SECTION"
    d, _ = ops.edit_section(d, d["sections"][0]["id"], title="Images", description="")
    assert d["sections"][0]["title"] == "Images" and d["sections"][0]["description"] is None
    ids = [s["id"] for s in d["sections"]]
    d, _ = ops.reorder_sections(d, [ids[2], ids[0], ids[1]])
    assert [s["title"] for s in d["sections"]] == ["Details", "Images", "Basics"]
    d, _ = ops.delete_section(d, ids[0])                                                                 # empty: deleted
    with pytest.raises(ops.BuilderError) as full:
        ops.delete_section(d, ids[2])
    assert full.value.code == "SECTION_NOT_EMPTY" and full.value.status == 409
    d, _ = ops.delete_section(d, ids[2], move_to=ids[1])                                                 # its fields move
    assert [s["title"] for s in d["sections"]] == ["Basics"] and keys(d) == [["title", "summary", "event_date"]]
    with pytest.raises(ops.BuilderError) as last:
        ops.delete_section(d, ids[1])
    assert last.value.code == "LAST_SECTION"


# --- projections -------------------------------------------------------------------------------------------------------------

def mixed_form():
    d = base_form()
    s = d["sections"][1]["id"]
    for raw in ({"key": "notes", "label": "Editor notes", "type": "long_text", "visibility": "enterprise_admin_only", "placeholder": "private"},
                {"key": "agenda", "label": "Agenda", "type": "supporting_document", "visibility": "published"},
                {"key": "teaser", "label": "Teaser", "type": "video", "visibility": "published", "enabled": False},
                {"key": "internal_files", "label": "Internal files", "type": "additional_images", "visibility": "enterprise_admin_only"},
                {"key": "audience", "label": "Audience", "type": "radio", "options": OPTIONS, "help_text": "Who is it for"}):
        d, _, _ = ops.add_field(d, s, raw)
    return d


def test_each_audience_gets_its_own_view_of_the_form():
    d = mixed_form()
    public = projection.project_form(d, "public")
    public_keys = [f["key"] for s in public["sections"] for f in s["fields"]]
    assert public_keys == ["title", "summary", "event_date", "agenda", "audience"]                     # no enterprise-only and no disabled field
    sample = public["sections"][0]["fields"][0]
    assert set(sample) == {"id", "key", "label", "type", "renderer", "order", "options"} and sample["type"] == "short_text"      # no rules, no help, no required

    admin = projection.project_form(d, "enterprise_admin")
    admin_keys = [f["key"] for s in admin["sections"] for f in s["fields"]]
    assert admin_keys == ["title", "summary", "event_date", "notes", "agenda", "internal_files", "audience"]                     # enterprise-only yes, disabled no
    notes = next(f for s in admin["sections"] for f in s["fields"] if f["key"] == "notes")
    assert notes["placeholder"] == "private" and notes["visibility"] == "enterprise_admin_only" and notes["type"] == "long_text" and notes["protected"] is False
    assert next(f for s in admin["sections"] for f in s["fields"] if f["key"] == "title")["protected"] is True and "system" not in notes

    builder = projection.project_form(d, "super_admin")
    assert [f["key"] for s in builder["sections"] for f in s["fields"]] == [f["key"] for f in ops.all_fields(d)] and "teaser" in str(builder)
    assert any(f["system"] for s in builder["sections"] for f in s["fields"])
    with pytest.raises(ValueError):
        projection.project_form(d, "customer")


def test_enterprise_only_values_and_media_never_reach_the_public():
    d = mixed_form()
    values = {"title": "Summit", "summary": "About", "event_date": "2026-12-01", "notes": "Call the venue first",
              "agenda": [{"url": "/api/v1/files/1", "name": "agenda.pdf", "size": 100, "content_type": "application/pdf", "file_id": "f-1"}],
              "internal_files": [{"url": "/api/v1/files/2", "name": "budget.png"}], "teaser": [{"url": "https://vimeo.com/76979871", "provider": "vimeo", "video_id": "76979871"}],
              "audience": "adults"}
    public = projection.project_values(d, values, "public")
    assert [v["key"] for v in public] == ["title", "summary", "event_date", "agenda", "audience"]
    assert "Call the venue" not in str(public) and "budget" not in str(public) and "f-1" not in str(public) and "vimeo" not in str(public)
    assert public[3] == {"key": "agenda", "label": "Agenda", "type": "supporting_document", "renderer": "document_uploader",
                         "value": [{"url": "/api/v1/files/1", "name": "agenda.pdf", "size": 100, "content_type": "application/pdf"}]}
    admin = projection.project_values(d, values, "enterprise_admin")
    assert [v["key"] for v in admin] == ["title", "summary", "event_date", "notes", "agenda", "internal_files", "audience"] and admin[3]["visibility"] == "enterprise_admin_only"
    assert admin[4]["value"][0]["file_id"] == "f-1"
    assert "teaser" in [v["key"] for v in projection.project_values(d, values, "super_admin")]
    assert projection.project_values(d, {"title": ""}, "public") == []
