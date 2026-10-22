"""The cleaner for the formatted text of blog and news posts."""
import pytest

from app.utils.html_sanitize import clean_html


def test_plain_text_and_allowed_formatting_pass_through():
    text = "<h2>Title</h2><p>Some <strong>bold</strong>, <em>italic</em> and a <a href=\"https://a.example/x\">link</a>.</p><ul><li>one</li></ul>"

    out = clean_html(text)

    assert "<h2>Title</h2>" in out and "<strong>bold</strong>" in out and "<li>one</li>" in out
    assert 'href="https://a.example/x"' in out and 'rel="noopener noreferrer nofollow"' in out
    assert clean_html("just words & more") == "just words &amp; more"


@pytest.mark.parametrize("dirty", [
    "<script>alert(1)</script>ok", "<p onclick=\"x()\">ok</p>", "<a href=\"javascript:alert(1)\">ok</a>", "<a href=\" JaVa\tScript:alert(1)\">ok</a>",
    "<img src=\"javascript:alert(1)\">ok", "<img src=x onerror=alert(1)>ok", "<iframe src=\"https://evil.example\"></iframe>ok",
    "<style>body{display:none}</style>ok", "<svg onload=alert(1)></svg>ok", "<a href=\"data:text/html;base64,AAAA\">ok</a>",
    "<a href=\"//evil.example/x\">ok</a>", "<form action=\"https://evil.example\"><input name=x></form>ok", "<object data=\"x\"></object>ok",
])
def test_scripts_handlers_and_unsafe_addresses_are_removed(dirty):
    out = clean_html(dirty).lower()

    assert "script" not in out and "onclick" not in out and "onerror" not in out and "onload" not in out
    assert "javascript" not in out and "iframe" not in out and "<style" not in out and "data:" not in out and "evil" not in out
    assert "ok" in out


def test_unknown_tags_lose_the_tag_but_keep_the_text_and_open_tags_are_closed():
    assert clean_html("<custom>kept</custom>") == "kept"
    assert clean_html("<p>one <b>two") == "<p>one <b>two</b></p>"
    assert clean_html("</b>stray") == "stray"
    assert clean_html(None) == ""


def test_attribute_values_cannot_break_out():
    out = clean_html('<a href="https://a.example/&quot; onmouseover=&quot;x">t</a>')

    assert "onmouseover" not in out.replace("&quot; onmouseover", "")  # only ever inside the escaped value
    assert out.count('href="') == 1


# --- the eBook profile -----------------------------------------------------------------------------------------------------------

def test_the_ebook_profile_keeps_tables_styles_and_picture_placeholders_but_not_images():
    from app.utils.html_sanitize import clean_html_report

    html, notes = clean_html_report(
        '<table><tr><td colspan="2" style="text-align:center">x</td></tr></table><div data-image-id="img_1"></div><p class="ql-align-center">y</p><img src="https://t/p.png">', "ebook")

    assert "<table>" in html and 'colspan="2"' in html and "text-align: center" in html and 'data-image-id="img_1"' in html and 'class="ql-align-center"' in html
    assert "<img" not in html and notes == ["removed <img> (add pictures as image blocks)"]


@pytest.mark.parametrize("style", ["background:url(javascript:x)", "position:fixed", "color:red;behavior:url(x.htc)", "width:expression(alert(1))", "font-size:9999px", "color:rgb(1,2,3);margin:0"])
def test_ebook_styles_outside_the_allow_list_are_dropped(style):
    from app.utils.html_sanitize import clean_html

    out = clean_html(f'<p style="{style}">x</p>', "ebook")

    for bad in ("url(", "position", "behavior", "expression", "9999", "margin"):
        assert bad not in out


def test_ebook_links_allow_anchors_but_not_scripts_and_the_blog_profile_does_not():
    from app.utils.html_sanitize import clean_html

    assert 'href="#chapter-2"' in clean_html('<a href="#chapter-2">go</a>', "ebook")
    assert "href" not in clean_html('<a href="#chapter-2">go</a>')
    assert "javascript" not in clean_html('<a href="javascript:alert(1)">x</a>', "ebook")
    assert "data-image-id" not in clean_html('<div data-image-id="img_1"></div>')
    assert "<img" in clean_html('<img src="https://ok.example/a.png" alt="a">')                       # the blog profile still allows pictures
