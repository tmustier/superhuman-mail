"""Adversarial lifecycle regression tests using fully synthetic mail data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from superhuman_mail import lifecycle

ACCOUNT = {"email": "owner@example.test", "provider_user_id": "user-fixture"}
THREAD = "thread_fixture"
DRAFT = "draft_fixture"
SID = "sid.fixture"
MESSAGE = "message_fixture"


def _userdata(*, draft=None, job=None, discarded=None, extra_messages=None):
    wrapper = {"draft": draft or {
        "id": DRAFT,
        "threadId": THREAD,
        "from": {"email": ACCOUNT["email"]},
        "to": [{"email": "recipient@example.test"}],
        "subject": "Fixture",
        "body": "<div>Hello</div>",
        "labelIds": ["DRAFT"],
    }}
    if job is not None:
        wrapper["sendJob"] = job
    if discarded is not None:
        wrapper["discardedAt"] = discarded
    messages = {DRAFT: wrapper}
    messages.update(extra_messages or {})
    return {"historyId": 42, "messages": messages}


def _provider(**overrides):
    value = {
        "id": MESSAGE,
        "labelIds": ["SENT"],
        "from": {"email": ACCOUNT["email"]},
        "date": "2026-07-10T12:00:00Z",
        "superhumanOwnDraftId": DRAFT,
        "superhumanId": SID,
    }
    value.update(overrides)
    return value


def _classify(*, draft=None, job=None, discarded=None, providers=(), attempt_sid=None):
    return lifecycle.classify(
        account=ACCOUNT,
        thread_id=THREAD,
        draft_id=DRAFT,
        userdata=_userdata(draft=draft, job=job, discarded=discarded),
        provider_messages=providers,
        observed_at="2026-07-10T12:01:00Z",
        attempt_superhuman_id=attempt_sid,
    )


def test_plain_active_draft_is_not_outbound_evidence():
    result = _classify()
    assert result["state"] == lifecycle.ACTIVE
    assert result["terminal"] is False
    assert result["outbound_evidence"] is False


def test_newer_draft_does_not_match_unrelated_sent_message_in_same_thread():
    unrelated = _provider(id="message_older", superhumanOwnDraftId="draft_older", superhumanId="sid.older")
    result = _classify(providers=[unrelated])
    assert result["state"] == lifecycle.ACTIVE
    assert result["outbound_evidence"] is False


def test_raw_draft_residue_plus_completed_job_and_provider_is_confirmed():
    result = _classify(
        job={"messageId": MESSAGE, "superhumanId": SID, "sentAt": "2026-07-10T12:00:00Z"},
        providers=[_provider()],
    )
    assert result["state"] == lifecycle.PROVIDER_CONFIRMED
    assert result["outbound_evidence"] is True
    assert result["provider_message"]["id"] == MESSAGE


def test_provider_proof_a_allows_absent_optional_linkage_but_rejects_mismatch():
    bare = _provider()
    bare.pop("superhumanOwnDraftId")
    bare.pop("superhumanId")
    job = {"messageId": MESSAGE, "superhumanId": SID, "sentAt": "2026-07-10T12:00:00Z"}
    assert _classify(job=job, providers=[bare])["state"] == lifecycle.PROVIDER_CONFIRMED

    mismatch = _provider(superhumanOwnDraftId="draft_other")
    result = _classify(job=job, providers=[mismatch])
    assert result["state"] == lifecycle.INCONSISTENT
    assert result["outbound_evidence"] is False


def test_provider_proof_b_requires_both_draft_and_superhuman_ids():
    provider = _provider()
    confirmed = _classify(job={"superhumanId": SID}, providers=[provider])
    assert confirmed["state"] == lifecycle.PROVIDER_CONFIRMED

    missing_sid = dict(provider)
    missing_sid.pop("superhumanId")
    result = _classify(job={"superhumanId": SID}, providers=[missing_sid])
    assert result["state"] == lifecycle.INCONSISTENT
    assert result["outbound_evidence"] is False


def test_provider_message_must_be_sent_and_from_bound_account():
    job = {"messageId": MESSAGE, "superhumanId": SID, "sentAt": "2026-07-10T12:00:00Z"}
    not_sent = _classify(job=job, providers=[_provider(labelIds=["INBOX"])])
    wrong_sender = _classify(job=job, providers=[_provider(**{"from": {"email": "other@example.test"}})])
    assert not_sent["state"] == lifecycle.BACKEND_CONFIRMED
    assert wrong_sender["state"] == lifecycle.BACKEND_CONFIRMED
    assert not_sent["outbound_evidence"] is False


def test_scheduled_for_without_job_remains_active():
    draft = _userdata()["messages"][DRAFT]["draft"] | {"scheduledFor": "2030-01-01T09:00:00Z"}
    result = _classify(draft=draft)
    assert result["state"] == lifecycle.ACTIVE


def test_future_send_job_with_scheduled_intent_is_scheduled():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    draft = _userdata()["messages"][DRAFT]["draft"] | {"scheduledFor": future}
    result = _classify(draft=draft, job={"sendAt": future, "superhumanId": SID})
    assert result["state"] == lifecycle.SCHEDULED
    assert result["outbound_evidence"] is False


def test_optimistic_and_undo_jobs_are_nonterminal():
    optimistic = _classify(job={"notSentToServer": True, "superhumanId": SID})
    undo = _classify(job={
        "sendAt": (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat(),
        "superhumanId": SID,
    })
    assert optimistic["state"] == lifecycle.REQUESTED
    assert undo["state"] == lifecycle.PENDING_UNDO
    assert optimistic["send_blocked"] and undo["send_blocked"]


def test_not_sent_to_server_never_counts_as_an_accepted_schedule():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    draft = _userdata()["messages"][DRAFT]["draft"] | {"scheduledFor": future}
    result = _classify(
        draft=draft,
        job={"sendAt": future, "superhumanId": SID, "notSentToServer": True},
    )
    assert result["state"] == lifecycle.REQUESTED
    assert result["outbound_evidence"] is False


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        ({"failedAt": "2026-07-10T12:00:00Z"}, lifecycle.FAILED),
        ({"abortedAt": "2026-07-10T12:00:00Z"}, lifecycle.ABORTED),
    ],
)
def test_terminal_failure_states(job, expected):
    result = _classify(job=job)
    assert result["state"] == expected
    assert result["terminal"] is True
    assert result["outbound_evidence"] is False


def test_discarded_source_is_terminal_not_outbound():
    result = _classify(discarded="2026-07-10T12:00:00Z")
    assert result["state"] == lifecycle.DISCARDED
    assert result["terminal"] is True
    assert result["outbound_evidence"] is False


def test_partial_backend_terminal_evidence_is_inconsistent():
    result = _classify(job={"superhumanId": SID, "sentAt": "2026-07-10T12:00:00Z"})
    assert result["state"] == lifecycle.INCONSISTENT
    assert result["outbound_evidence"] is False


def test_backend_terminal_without_provider_is_not_business_outbound_proof():
    result = _classify(job={"messageId": MESSAGE, "superhumanId": SID, "sentAt": "2026-07-10T12:00:00Z"})
    assert result["state"] == lifecycle.BACKEND_CONFIRMED
    assert result["outbound_evidence"] is False


def test_provider_confirmation_survives_source_draft_cleanup():
    userdata = {
        "historyId": 43,
        "messages": {DRAFT: {"sendJob": {
            "messageId": MESSAGE,
            "superhumanId": SID,
            "sentAt": "2026-07-10T12:00:00Z",
        }}},
    }
    result = lifecycle.classify(
        account=ACCOUNT,
        thread_id=THREAD,
        draft_id=DRAFT,
        userdata=userdata,
        provider_messages=[_provider()],
        observed_at="2026-07-10T12:01:00Z",
    )
    assert result["state"] == lifecycle.PROVIDER_CONFIRMED
    assert result["outbound_evidence"] is True


def test_provider_can_confirm_when_userdata_message_id_lags_using_attempt_identity():
    result = _classify(job={}, providers=[_provider()], attempt_sid=SID)
    assert result["state"] == lifecycle.PROVIDER_CONFIRMED
    assert result["outbound_evidence"] is True


def test_backend_success_plus_failure_without_provider_is_inconsistent():
    result = _classify(job={
        "messageId": MESSAGE,
        "superhumanId": SID,
        "sentAt": "2026-07-10T12:00:00Z",
        "failedAt": "2026-07-10T12:00:01Z",
    })
    assert result["state"] == lifecycle.INCONSISTENT
    assert result["consistency"] == "conflicting"
    assert result["outbound_evidence"] is False


def test_provider_confirmation_wins_material_outcome_but_surfaces_stale_failure():
    result = _classify(
        job={
            "messageId": MESSAGE,
            "superhumanId": SID,
            "sentAt": "2026-07-10T12:00:00Z",
            "failedAt": "2026-07-10T12:00:01Z",
        },
        providers=[_provider()],
    )
    assert result["state"] == lifecycle.PROVIDER_CONFIRMED
    assert result["consistency"] == "conflicting"
    assert result["outbound_evidence"] is True


def test_draft_id_and_draft_label_never_set_outbound_evidence():
    draft = _userdata()["messages"][DRAFT]["draft"] | {
        "date": "2026-07-10T12:00:00Z",
        "clientCreatedAt": 1783684800000,
        "labelIds": ["DRAFT", "SENT"],
    }
    result = _classify(draft=draft)
    assert result["state"] == lifecycle.ACTIVE
    assert result["outbound_evidence"] is False
