"""Tests for forward draft quoted-content generation."""
from __future__ import annotations

from unittest.mock import patch

from superhuman_mail.draft import _build_forward_quoted_content, create_forward


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contact(email: str, name: str | None = None) -> dict[str, str]:
    return {"email": email, "name": name or email.split("@")[0]}


def _raw_msg(
    *,
    msg_id: str = "msg-1",
    sender: dict[str, str] | None = None,
    to: list[dict[str, str]] | None = None,
    cc: list[dict[str, str]] | None = None,
    subject: str = "Quarterly update",
    date: str = "2026-03-11T23:00:18.000Z",
    snippet: str = "Fallback snippet",
    rfc822_id: str = "<msg-1@example.com>",
) -> dict:
    return {
        "id": msg_id,
        "from": sender or _contact("alice@example.com", "Alice Example"),
        "to": [_contact("bob@example.com", "Bob Buyer")] if to is None else to,
        "cc": [_contact("carol@example.com", "Carol Copy")] if cc is None else cc,
        "subject": subject,
        "date": date,
        "snippet": snippet,
        "rfc822Id": rfc822_id,
    }


# ---------------------------------------------------------------------------
# _build_forward_quoted_content
# ---------------------------------------------------------------------------


class TestBuildForwardQuotedContent:
    def test_uses_rendered_html_and_formats_forward_header(self):
        raw = _raw_msg()
        rendered = {"body": {"text": "Plain body", "html": "<div><b>Rendered HTML</b></div>"}}

        quoted = _build_forward_quoted_content(raw, rendered)

        assert "---------- Forwarded message ----------" in quoted
        assert "From: Alice Example &lt;alice@example.com&gt;" in quoted
        assert 'dateTime="2026-03-11T23:00:18.000Z"' in quoted
        assert "Wednesday, March 11 2026 at 11:00 PM GMT" in quoted
        assert "Subject: Quarterly update" in quoted
        assert "To: Bob Buyer &lt;bob@example.com&gt;" in quoted
        assert "Cc: Carol Copy &lt;carol@example.com&gt;" in quoted
        assert "<div><b>Rendered HTML</b></div>" in quoted

    def test_prefers_raw_body_before_snippet_when_rendered_body_is_unavailable(self):
        raw = _raw_msg(cc=[], snippet="Line one\n\nLine two")
        raw["body"] = {"text": "Longer raw body", "html": ""}

        quoted = _build_forward_quoted_content(raw, None)

        assert "Cc:" not in quoted
        assert "Longer raw body" in quoted
        assert "Line one<br><br>Line two" not in quoted


# ---------------------------------------------------------------------------
# create_forward
# ---------------------------------------------------------------------------


class TestCreateForward:
    def test_inlines_quoted_content_into_forward_body(self):
        raw = _raw_msg(subject="Launch plan", snippet="Fallback body")
        rendered = {
            "body": {
                "text": "Rendered body",
                "html": "",
            }
        }
        captured: dict[str, object] = {}

        def _fake_write(draft: dict, command: str):
            captured["draft"] = draft
            captured["command"] = command
            return {"status": "succeeded", "command": command, "data": {"draft_id": draft["id"]}, "errors": [], "warnings": []}

        with (
            patch("superhuman_mail.draft._local.get_thread_json", return_value={"messages": [raw]}),
            patch("superhuman_mail.draft._local.get_messages", return_value=[rendered]),
            patch("superhuman_mail.draft._default_from", return_value={"email": "me@example.com", "name": "Me Example"}),
            patch("superhuman_mail.draft._config.timezone", return_value="Europe/London"),
            patch("superhuman_mail.draft._write_draft", side_effect=_fake_write),
        ):
            result = create_forward("thread-1", "FYI — see below", to=["dest@example.com"])

        assert result["status"] == "succeeded"
        assert captured["command"] == "draft.forward"

        draft = captured["draft"]
        assert isinstance(draft, dict)
        assert draft["action"] == "forward"
        assert draft["quotedContentInlined"] is True
        assert "---------- Forwarded message ----------" in draft["quotedContent"]
        assert "Rendered body" in draft["quotedContent"]
        assert draft["body"].startswith("<div>FYI — see below</div><br><div class=\"sh-quoted-content sh-color-black sh-color\">")
        assert draft["quotedContent"] in draft["body"]
        assert draft["to"] == [{"email": "dest@example.com", "name": "dest@example.com"}]
        assert draft["references"] == ["<msg-1@example.com>"]

    def test_forward_still_builds_quote_if_message_rendering_lookup_fails(self):
        raw = _raw_msg(cc=[], snippet="Snippet fallback")
        captured: dict[str, object] = {}

        def _fake_write(draft: dict, command: str):
            captured["draft"] = draft
            return {"status": "succeeded", "command": command, "data": {}, "errors": [], "warnings": []}

        with (
            patch("superhuman_mail.draft._local.get_thread_json", return_value={"messages": [raw]}),
            patch("superhuman_mail.draft._local.get_messages", side_effect=RuntimeError("fts unavailable")),
            patch("superhuman_mail.draft._default_from", return_value={"email": "me@example.com", "name": "Me Example"}),
            patch("superhuman_mail.draft._config.timezone", return_value="Europe/London"),
            patch("superhuman_mail.draft._write_draft", side_effect=_fake_write),
        ):
            create_forward("thread-1", "FYI")

        draft = captured["draft"]
        assert isinstance(draft, dict)
        assert "Snippet fallback" in draft["quotedContent"]
        assert "Cc:" not in draft["quotedContent"]
        assert draft["quotedContentInlined"] is True

    def test_skips_superhuman_system_messages_when_choosing_forward_content(self):
        customer = _raw_msg(
            msg_id="msg-customer",
            sender=_contact("alice@example.com", "Alice Example"),
            subject="Customer thread",
            snippet="Customer body",
            rfc822_id="<customer@example.com>",
        )
        reminder = _raw_msg(
            msg_id="msg-reminder",
            sender=_contact("reminder@superhuman.com", "Superhuman Reminder"),
            to=[],
            cc=[],
            subject="Reminder",
            snippet="Reminder body",
            rfc822_id="<reminder@superhuman.com>",
        )
        captured: dict[str, object] = {}

        def _fake_write(draft: dict, command: str):
            captured["draft"] = draft
            return {"status": "succeeded", "command": command, "data": {}, "errors": [], "warnings": []}

        with (
            patch("superhuman_mail.draft._local.get_thread_json", return_value={"messages": [customer, reminder]}),
            patch(
                "superhuman_mail.draft._local.get_messages",
                return_value=[
                    {"body": {"text": "Customer rendered body", "html": ""}},
                    {"body": {"text": "Reminder rendered body", "html": ""}},
                ],
            ),
            patch("superhuman_mail.draft._default_from", return_value={"email": "me@example.com", "name": "Me Example"}),
            patch("superhuman_mail.draft._config.timezone", return_value="Europe/London"),
            patch("superhuman_mail.draft._write_draft", side_effect=_fake_write),
        ):
            create_forward("thread-1", "FYI")

        draft = captured["draft"]
        assert isinstance(draft, dict)
        assert "Alice Example &lt;alice@example.com&gt;" in draft["quotedContent"]
        assert "Superhuman Reminder" not in draft["quotedContent"]
        assert draft["subject"] == "Fwd: Customer thread"
        assert draft["inReplyTo"] == "msg-customer"
        assert draft["references"] == ["<customer@example.com>"]
