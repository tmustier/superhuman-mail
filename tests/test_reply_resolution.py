"""Tests for reply-message resolution — skip system & internal-only messages."""
from __future__ import annotations

from unittest.mock import patch

from superhuman_mail.draft import (
    _find_reply_message,
    _is_internal_only,
    _is_system_sender,
    _msg_participants,
    _thread_has_external,
)


# ---------------------------------------------------------------------------
# Helpers to build message fixtures
# ---------------------------------------------------------------------------

def _msg(from_email: str, to: list[str] | None = None, cc: list[str] | None = None, **kw) -> dict:
    m: dict = {
        "from": {"email": from_email, "name": from_email.split("@")[0]},
        "to": [{"email": e, "name": e.split("@")[0]} for e in (to or [])],
        "cc": [{"email": e, "name": e.split("@")[0]} for e in (cc or [])],
        "subject": "Test",
        "id": kw.get("id", from_email),
    }
    m.update(kw)
    return m


# ---------------------------------------------------------------------------
# _is_system_sender
# ---------------------------------------------------------------------------

class TestIsSystemSender:
    def test_reminder(self):
        assert _is_system_sender(_msg("reminder@superhuman.com")) is True

    def test_sharing(self):
        assert _is_system_sender(_msg("sharing@superhuman.com")) is True

    def test_any_superhuman(self):
        assert _is_system_sender(_msg("anything@superhuman.com")) is True

    def test_normal_sender(self):
        assert _is_system_sender(_msg("alice@example.com")) is False

    def test_empty_from(self):
        assert _is_system_sender({"from": None}) is False

    def test_missing_from(self):
        assert _is_system_sender({}) is False


# ---------------------------------------------------------------------------
# _msg_participants
# ---------------------------------------------------------------------------

class TestMsgParticipants:
    def test_collects_from_to_cc(self):
        m = _msg("a@x.com", to=["b@x.com"], cc=["c@y.com"])
        assert set(_msg_participants(m)) == {"a@x.com", "b@x.com", "c@y.com"}

    def test_empty(self):
        assert _msg_participants({"from": {}}) == []


# ---------------------------------------------------------------------------
# _is_internal_only
# ---------------------------------------------------------------------------

class TestIsInternalOnly:
    def test_all_internal(self):
        m = _msg("alice@acme.com", to=["bob@acme.com"])
        assert _is_internal_only(m, "acme.com") is True

    def test_has_external(self):
        m = _msg("alice@acme.com", to=["bob@acme.com", "eve@vendor.com"])
        assert _is_internal_only(m, "acme.com") is False

    def test_sender_external(self):
        m = _msg("eve@vendor.com", to=["alice@acme.com"])
        assert _is_internal_only(m, "acme.com") is False


# ---------------------------------------------------------------------------
# _thread_has_external
# ---------------------------------------------------------------------------

class TestThreadHasExternal:
    def test_external_present(self):
        msgs = [
            _msg("eve@vendor.com", to=["alice@acme.com"]),
            _msg("alice@acme.com", to=["eve@vendor.com"]),
        ]
        assert _thread_has_external(msgs, "acme.com") is True

    def test_all_internal(self):
        msgs = [
            _msg("alice@acme.com", to=["bob@acme.com"]),
            _msg("bob@acme.com", to=["alice@acme.com"]),
        ]
        assert _thread_has_external(msgs, "acme.com") is False

    def test_ignores_superhuman_messages(self):
        msgs = [
            _msg("alice@acme.com", to=["bob@acme.com"]),
            _msg("reminder@superhuman.com", to=["alice@acme.com"]),
        ]
        assert _thread_has_external(msgs, "acme.com") is False


# ---------------------------------------------------------------------------
# _find_reply_message
# ---------------------------------------------------------------------------

def _patched_find(messages, domain="acme.com"):
    """Run _find_reply_message with a mocked internal domain."""
    with patch("superhuman_mail.draft._internal_domain", return_value=domain):
        return _find_reply_message(messages)


class TestFindReplyMessage:
    def test_skips_superhuman_system_message(self):
        """Last message is a reminder — should reply to the real message."""
        real = _msg("eve@vendor.com", to=["me@acme.com"], id="real")
        system = _msg("reminder@superhuman.com", to=["me@acme.com"], id="system")
        assert _patched_find([real, system])["id"] == "real"

    def test_skips_multiple_system_messages(self):
        real = _msg("eve@vendor.com", to=["me@acme.com"], id="real")
        sys1 = _msg("reminder@superhuman.com", to=["me@acme.com"], id="s1")
        sys2 = _msg("sharing@superhuman.com", to=["me@acme.com"], id="s2")
        assert _patched_find([real, sys1, sys2])["id"] == "real"

    def test_skips_internal_only_in_external_thread(self):
        """Internal forward after external message — reply to external."""
        external = _msg("eve@vendor.com", to=["me@acme.com"], id="external")
        internal = _msg("me@acme.com", to=["colleague@acme.com"], id="internal")
        assert _patched_find([external, internal])["id"] == "external"

    def test_keeps_internal_in_internal_thread(self):
        """Fully internal thread — reply to the last message."""
        m1 = _msg("alice@acme.com", to=["bob@acme.com"], id="m1")
        m2 = _msg("bob@acme.com", to=["alice@acme.com"], id="m2")
        assert _patched_find([m1, m2])["id"] == "m2"

    def test_combined_system_and_internal(self):
        """External msg, then internal forward, then system reminder."""
        external = _msg("eve@vendor.com", to=["me@acme.com"], id="external")
        internal = _msg("me@acme.com", to=["colleague@acme.com"], id="internal")
        system = _msg("reminder@superhuman.com", to=["me@acme.com"], id="system")
        assert _patched_find([external, internal, system])["id"] == "external"

    def test_no_messages_raises(self):
        import pytest
        with pytest.raises(RuntimeError, match="no messages"):
            _patched_find([])

    def test_all_system_falls_back_to_last(self):
        """If every message is system, fall back to raw last."""
        s1 = _msg("reminder@superhuman.com", id="s1")
        s2 = _msg("sharing@superhuman.com", id="s2")
        assert _patched_find([s1, s2])["id"] == "s2"

    def test_no_internal_domain_still_skips_system(self):
        """If domain detection fails, still skip system messages."""
        real = _msg("eve@vendor.com", to=["me@acme.com"], id="real")
        system = _msg("reminder@superhuman.com", id="system")
        with patch("superhuman_mail.draft._internal_domain", return_value=None):
            assert _find_reply_message([real, system])["id"] == "real"

    def test_normal_thread_returns_last(self):
        """Normal thread with no system messages — returns last message."""
        m1 = _msg("eve@vendor.com", to=["me@acme.com"], id="m1")
        m2 = _msg("me@acme.com", to=["eve@vendor.com"], id="m2")
        assert _patched_find([m1, m2])["id"] == "m2"

    def test_all_internal_filtered_falls_back_to_last_non_system(self):
        """External thread but all recent non-system msgs are internal — falls back."""
        external = _msg("eve@vendor.com", to=["me@acme.com"], id="external")
        int1 = _msg("me@acme.com", to=["colleague@acme.com"], id="int1")
        int2 = _msg("colleague@acme.com", to=["me@acme.com"], id="int2")
        # external is the only one with an external participant
        assert _patched_find([external, int1, int2])["id"] == "external"
