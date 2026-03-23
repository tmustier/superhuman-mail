"""Tests for smart-send fields — abort_on_reply, reminder, sensitivity labels."""
from __future__ import annotations

from superhuman_mail.draft import _apply_smart_send
from superhuman_mail.send import _build_outgoing


class TestApplySmartSend:
    def test_no_fields_set(self):
        draft: dict = {}
        _apply_smart_send(draft)
        assert "scheduledFor" not in draft
        assert "abortOnReply" not in draft
        assert "reminder" not in draft

    def test_scheduled_for(self):
        draft: dict = {}
        _apply_smart_send(draft, scheduled_for="2026-04-01T09:00:00Z")
        assert draft["scheduledFor"] == "2026-04-01T09:00:00Z"

    def test_abort_on_reply(self):
        draft: dict = {}
        _apply_smart_send(draft, abort_on_reply=True)
        assert draft["abortOnReply"] is True

    def test_abort_on_reply_false_not_set(self):
        draft: dict = {}
        _apply_smart_send(draft, abort_on_reply=False)
        assert "abortOnReply" not in draft

    def test_reminder(self):
        draft: dict = {}
        _apply_smart_send(draft, reminder="2026-04-03T09:00:00Z")
        assert draft["reminder"] == "2026-04-03T09:00:00Z"

    def test_sensitivity_labels(self):
        draft: dict = {}
        _apply_smart_send(draft, sensitivity_label_id="lbl_123", sensitivity_tenant_id="ten_456")
        assert draft["sensitivityLabelId"] == "lbl_123"
        assert draft["sensitivityTenantId"] == "ten_456"

    def test_all_fields(self):
        draft: dict = {}
        _apply_smart_send(
            draft,
            scheduled_for="2026-04-01T09:00:00Z",
            abort_on_reply=True,
            reminder="2026-04-03T09:00:00Z",
            sensitivity_label_id="lbl_123",
            sensitivity_tenant_id="ten_456",
        )
        assert draft["scheduledFor"] == "2026-04-01T09:00:00Z"
        assert draft["abortOnReply"] is True
        assert draft["reminder"] == "2026-04-03T09:00:00Z"
        assert draft["sensitivityLabelId"] == "lbl_123"
        assert draft["sensitivityTenantId"] == "ten_456"


class TestBuildOutgoing:
    """Verify smart-send fields propagate from draft to outgoing payload."""

    def _minimal_draft(self, **overrides) -> dict:
        base = {
            "id": "draft001",
            "threadId": "thread001",
            "from": {"email": "me@acme.com", "name": "Me"},
            "to": [{"email": "you@example.com"}],
            "cc": [],
            "bcc": [],
            "subject": "Test",
            "body": "<div>Hello</div>",
            "rfc822Id": "<test@we.are.superhuman.com>",
            "attachments": [],
        }
        base.update(overrides)
        return base

    def test_abort_on_reply_propagates(self):
        draft = self._minimal_draft(abortOnReply=True)
        out = _build_outgoing(draft)
        assert out["abort_on_reply"] is True

    def test_scheduled_for_propagates(self):
        draft = self._minimal_draft(scheduledFor="2026-04-01T09:00:00Z")
        out = _build_outgoing(draft)
        assert out["scheduled_for"] == "2026-04-01T09:00:00Z"

    def test_reminder_propagates(self):
        draft = self._minimal_draft(reminder="2026-04-03T09:00:00Z")
        out = _build_outgoing(draft)
        assert out["reminder"] == "2026-04-03T09:00:00Z"

    def test_sensitivity_labels_propagate(self):
        draft = self._minimal_draft(sensitivityLabelId="lbl_123", sensitivityTenantId="ten_456")
        out = _build_outgoing(draft)
        assert out["sensitivity_label_id"] == "lbl_123"
        assert out["sensitivity_tenant_id"] == "ten_456"

    def test_unset_fields_absent(self):
        draft = self._minimal_draft()
        out = _build_outgoing(draft)
        assert "abort_on_reply" not in out
        assert "scheduled_for" not in out
        assert "reminder" not in out
        assert "sensitivity_label_id" not in out
        assert "sensitivity_tenant_id" not in out
