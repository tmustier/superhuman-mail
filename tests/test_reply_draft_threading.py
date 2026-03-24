"""Tests for reply draft threading metadata selection."""
from __future__ import annotations

from unittest.mock import patch

from superhuman_mail.draft import create_reply


def _contact(email: str, name: str | None = None) -> dict[str, str]:
    return {"email": email, "name": name or email.split("@")[0]}


def _raw_msg(
    *,
    msg_id: str,
    sender: str,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    subject: str = "Test subject",
    rfc822_id: str,
) -> dict:
    return {
        "id": msg_id,
        "from": _contact(sender),
        "to": [_contact(e) for e in (to or [])],
        "cc": [_contact(e) for e in (cc or [])],
        "subject": subject,
        "rfc822Id": rfc822_id,
    }


class TestCreateReplyThreading:
    def test_uses_real_message_not_sharing_message_for_threading_headers(self):
        external = _raw_msg(
            msg_id="customer-msg",
            sender="eve@vendor.com",
            to=["me@acme.com"],
            subject="Customer thread",
            rfc822_id="<customer@example.com>",
        )
        sharing = _raw_msg(
            msg_id="share-msg",
            sender="sharing@superhuman.com",
            to=["me@acme.com"],
            subject="Customer thread",
            rfc822_id="<share@superhuman.com>",
        )
        captured: dict[str, object] = {}

        def _fake_write(draft: dict, command: str):
            captured["draft"] = draft
            return {"status": "succeeded", "command": command, "data": {}, "errors": [], "warnings": []}

        with (
            patch("superhuman_mail.draft._local.get_thread_json", return_value={"messages": [external, sharing]}),
            patch("superhuman_mail.draft._default_from", return_value={"email": "me@acme.com", "name": "Me"}),
            patch("superhuman_mail.draft._internal_domain", return_value="acme.com"),
            patch("superhuman_mail.draft._config.timezone", return_value="Europe/London"),
            patch("superhuman_mail.draft._write_draft", side_effect=_fake_write),
        ):
            create_reply("thread-1", "Thanks")

        draft = captured["draft"]
        assert isinstance(draft, dict)
        assert draft["inReplyTo"] == "customer-msg"
        assert draft["inReplyToRfc822Id"] == "<customer@example.com>"
        assert draft["references"] == ["<customer@example.com>"]
        assert [c["email"] for c in draft["to"]] == ["eve@vendor.com"]

    def test_follow_up_on_self_sent_external_message_skips_internal_forward(self):
        external_self = _raw_msg(
            msg_id="external-self",
            sender="me@acme.com",
            to=["eve@vendor.com"],
            subject="Customer thread",
            rfc822_id="<external-self@example.com>",
        )
        internal_forward = _raw_msg(
            msg_id="internal-forward",
            sender="me@acme.com",
            to=["colleague@acme.com"],
            subject="Fwd: Customer thread",
            rfc822_id="<internal-forward@example.com>",
        )
        captured: dict[str, object] = {}

        def _fake_write(draft: dict, command: str):
            captured["draft"] = draft
            return {"status": "succeeded", "command": command, "data": {}, "errors": [], "warnings": []}

        with (
            patch("superhuman_mail.draft._local.get_thread_json", return_value={"messages": [external_self, internal_forward]}),
            patch("superhuman_mail.draft._default_from", return_value={"email": "me@acme.com", "name": "Me"}),
            patch("superhuman_mail.draft._internal_domain", return_value="acme.com"),
            patch("superhuman_mail.draft._config.timezone", return_value="Europe/London"),
            patch("superhuman_mail.draft._write_draft", side_effect=_fake_write),
        ):
            create_reply("thread-1", "Following up")

        draft = captured["draft"]
        assert isinstance(draft, dict)
        assert draft["inReplyTo"] == "external-self"
        assert draft["inReplyToRfc822Id"] == "<external-self@example.com>"
        assert draft["references"] == ["<external-self@example.com>"]
        assert [c["email"] for c in draft["to"]] == ["eve@vendor.com"]
