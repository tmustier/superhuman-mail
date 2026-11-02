"""Tests for Superhuman comment HTML rendering."""
from __future__ import annotations

from superhuman_mail.comment import _build_html


def test_comment_html_preserves_blank_lines_between_paragraphs():
    html = _build_html("Summary:\n- one\n- two\n\nNext step:\nfollow up")

    assert html == (
        "<div>"
        "<p>Summary:</p><p>- one</p><p>- two</p>"
        "<p>\u00a0</p><p>Next step:</p><p>follow up</p>"
        "</div>"
    )
    assert "<br" not in html


def test_comment_html_normalizes_windows_newlines():
    html = _build_html("Line one\r\nLine two\r\n\r\nLine three")

    assert html == "<div><p>Line one</p><p>Line two</p><p>\u00a0</p><p>Line three</p></div>"


def test_comment_html_keeps_escaping_and_mentions_across_lines():
    html = _build_html(
        "Please review @Alice Example\nUse <real> data",
        mentions=[{"email": "alice@example.com", "fullName": "Alice Example"}],
    )

    assert '<a data-mention="alice@example.com" data-name="Alice Example">@Alice Example</a>\u200b' in html
    assert "<p>Use &lt;real&gt; data</p>" in html


def test_empty_comment_retains_valid_placeholder():
    assert _build_html("\n\n") == "<div><p></p></div>"
    assert _build_html(" \t\n \t") == "<div><p></p></div>"


def test_comment_html_preserves_multiple_blank_lines_and_trims_outer_newlines():
    assert _build_html("\nFirst\n \t\n\nSecond\n") == (
        "<div><p>First</p><p>\u00a0</p><p>\u00a0</p><p>Second</p></div>"
    )
