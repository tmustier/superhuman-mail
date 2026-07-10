"""Draft reads keep source objects but cannot hide terminal lifecycle."""
from unittest.mock import patch

from superhuman_mail import draft, lifecycle


def _state(draft_id, state):
    return {
        "draft_id": draft_id,
        "state": state,
        "terminal": state in lifecycle.TERMINAL_STATES,
        "send_blocked": state in lifecycle.BLOCKING_STATES,
        "outbound_evidence": state == lifecycle.PROVIDER_CONFIRMED,
    }


def _observed():
    return {
        "account": {"email": "owner@example.test", "provider_user_id": "user-fixture"},
        "thread_id": "thread-fixture",
        "userdata": {
            "messages": {
                "draft_active": {"draft": {"id": "draft_active", "body": "active"}},
                "draft_sent": {"draft": {"id": "draft_sent", "body": "residue"}},
                "draft_pending": {"draft": {"id": "draft_pending", "body": "pending"}},
            }
        },
        "lifecycle_by_draft_id": {
            "draft_active": _state("draft_active", lifecycle.ACTIVE),
            "draft_sent": _state("draft_sent", lifecycle.PROVIDER_CONFIRMED),
            "draft_pending": _state("draft_pending", lifecycle.PENDING_UNDO),
        },
    }, []


def test_read_exposes_active_terminal_and_pending_counts():
    with patch("superhuman_mail.draft.lifecycle.observe_thread", return_value=_observed()):
        result = draft.read("thread-fixture")
    assert result["status"] == "succeeded"
    assert result["data"]["draft_count"] == 3
    assert result["data"]["active_draft_count"] == 1
    assert result["data"]["terminal_draft_count"] == 1
    assert result["data"]["nonterminal_blocked_draft_count"] == 1
    assert result["data"]["lifecycle_by_draft_id"]["draft_sent"]["outbound_evidence"] is True
    assert "terminal source draft" in result["warnings"][0]


def test_active_only_hides_terminal_and_pending_source_residue():
    with patch("superhuman_mail.draft.lifecycle.observe_thread", return_value=_observed()):
        result = draft.read("thread-fixture", active_only=True)
    assert [item["id"] for item in result["data"]["drafts"]] == ["draft_active"]
    assert result["data"]["active_draft_count"] == 1
    assert result["data"]["terminal_draft_count"] == 1


def test_status_returns_lifecycle_without_source_draft_body():
    with patch("superhuman_mail.draft.lifecycle.observe_thread", return_value=_observed()):
        result = draft.status("thread-fixture", "draft_sent")
    assert result["status"] == "succeeded"
    assert result["data"]["lifecycle_by_draft_id"]["draft_sent"]["state"] == lifecycle.PROVIDER_CONFIRMED
    assert "drafts" not in result["data"]
