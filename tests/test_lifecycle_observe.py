"""Live-join lifecycle observation tests; all data sources are synthetic."""
from __future__ import annotations

from unittest.mock import patch

from superhuman_mail import lifecycle

ACCOUNT = {"email": "owner@example.test", "provider_user_id": "user-fixture"}
THREAD = "draft_thread_fixture"
REPLACEMENT = "provider_thread_fixture"
DRAFT = "draft_fixture"
MESSAGE = "message_fixture"
SID = "sid.fixture"


def _userdata(*, replacement=None):
    data = {
        "historyId": 42,
        "messages": {
            DRAFT: {
                "draft": {
                    "id": DRAFT,
                    "threadId": THREAD,
                    "from": {"email": ACCOUNT["email"]},
                    "to": [{"email": "recipient@example.test"}],
                    "subject": "Fixture",
                    "body": "<div>Hello</div>",
                    "labelIds": ["DRAFT"],
                },
                "sendJob": {
                    "messageId": MESSAGE,
                    "superhumanId": SID,
                    "sentAt": "2026-07-10T12:00:00Z",
                },
            }
        },
    }
    if replacement:
        data["threadReplacement"] = replacement
    return data


def _provider():
    return {
        "id": MESSAGE,
        "labelIds": ["SENT"],
        "from": {"email": ACCOUNT["email"]},
        "date": "2026-07-10T12:00:01Z",
        "superhumanOwnDraftId": DRAFT,
        "superhumanId": SID,
    }


def test_observe_thread_follows_synthetic_thread_replacement_for_provider_proof():
    userdata = _userdata(replacement=REPLACEMENT)
    with patch("superhuman_mail.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
        with patch("superhuman_mail.lifecycle._thread.userdata_raw", return_value=userdata):
            with patch(
                "superhuman_mail.lifecycle._local.get_thread_json",
                side_effect=[{"messages": []}, {"messages": [_provider()]}],
            ) as local_read:
                observed, warnings = lifecycle.observe_thread(
                    THREAD,
                    account=ACCOUNT["email"],
                    require_explicit_account=True,
                )
    assert warnings == []
    assert [call.args[0] for call in local_read.call_args_list] == [THREAD, REPLACEMENT]
    assert observed["provider_thread_id"] == REPLACEMENT
    assert observed["lifecycle_by_draft_id"][DRAFT]["state"] == lifecycle.PROVIDER_CONFIRMED
    assert observed["lifecycle_by_draft_id"][DRAFT]["provider_message"]["thread_id"] == REPLACEMENT


def test_observe_returns_exact_wrapper_and_provider_cache_warning():
    userdata = _userdata()
    with patch("superhuman_mail.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
        with patch("superhuman_mail.lifecycle._thread.userdata_raw", return_value=userdata):
            with patch(
                "superhuman_mail.lifecycle._local.get_thread_json",
                side_effect=RuntimeError("cache offline"),
            ):
                state, wrapper, warnings = lifecycle.observe(
                    THREAD,
                    DRAFT,
                    account=ACCOUNT["email"],
                    require_explicit_account=True,
                    attempt_superhuman_id=SID,
                )
    assert wrapper is userdata["messages"][DRAFT]
    assert state["state"] == lifecycle.BACKEND_CONFIRMED
    assert state["outbound_evidence"] is False
    assert warnings == [f"Provider cache unavailable for {THREAD}: cache offline"]
