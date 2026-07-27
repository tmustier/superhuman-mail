"""Tests for Superhuman comment HTML rendering."""
from __future__ import annotations

from superhuman_mail.comment import _build_html


def test_comment_html_renders_each_nonempty_line_as_a_paragraph():
    html = _build_html("Summary:\n- one\n- two\n\nNext step:\nfollow up")

    assert html == (
        "<div>"
        "<p>Summary:</p><p>- one</p><p>- two</p>"
        "<p>Next step:</p><p>follow up</p>"
        "</div>"
    )
    assert "<br" not in html


def test_comment_html_normalizes_windows_newlines():
    html = _build_html("Line one\r\nLine two\r\n\r\nLine three")

    assert html == "<div><p>Line one</p><p>Line two</p><p>Line three</p></div>"


def test_comment_html_keeps_escaping_and_mentions_across_lines():
    html = _build_html(
        "Please review @Alice Example\nUse <real> data",
        mentions=[{"email": "alice@example.com", "fullName": "Alice Example"}],
    )

    assert '<a data-mention="alice@example.com" data-name="Alice Example">@Alice Example</a>\u200b' in html
    assert "<p>Use &lt;real&gt; data</p>" in html


def test_empty_comment_retains_valid_placeholder():
    assert _build_html("\n\n") == "<div><p></p></div>"
