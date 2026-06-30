"""Tests for Superhuman comment HTML rendering."""
from __future__ import annotations

from superhuman_mail.comment import _build_html


def test_comment_html_preserves_single_newlines_as_breaks():
    html = _build_html("Summary:\n- one\n- two\n\nNext step:\nfollow up")

    assert html == (
        "<div>"
        "<p>Summary:<br />- one<br />- two</p>"
        "<p>Next step:<br />follow up</p>"
        "</div>"
    )


def test_comment_html_normalizes_windows_newlines():
    html = _build_html("Line one\r\nLine two\r\n\r\nLine three")

    assert html == "<div><p>Line one<br />Line two</p><p>Line three</p></div>"


def test_comment_html_keeps_escaping_and_mentions_with_line_breaks():
    html = _build_html(
        "Please review @Alice Example\nUse <real> data",
        mentions=[{"email": "alice@example.com", "fullName": "Alice Example"}],
    )

    assert '<a data-mention="alice@example.com" data-name="Alice Example">@Alice Example</a>\u200b' in html
    assert "<br />Use &lt;real&gt; data" in html
