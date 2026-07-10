"""Execute-time send guard regression tests; all transports are fake."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from superhuman_mail import lifecycle, send

THREAD = "thread_fixture"
DRAFT = "draft_fixture"
ACCOUNT = {"email": "owner@example.test", "provider_user_id": "user-fixture"}


def _draft(**overrides):
    value = {
        "id": DRAFT,
        "threadId": THREAD,
        "action": "reply",
        "from": {"email": ACCOUNT["email"]},
        "to": [{"email": "recipient@example.test"}],
        "cc": [],
        "bcc": [],
        "subject": "Fixture",
        "body": "<div>Hello</div>",
        "attachments": [],
        "rfc822Id": "<fixture@example.test>",
    }
    value.update(overrides)
    return value


def _observed(state=lifecycle.ACTIVE, *, draft=None):
    lifecycle_data = {
        "account": ACCOUNT,
        "thread_id": THREAD,
        "draft_id": DRAFT,
        "state": state,
        "terminal": state in lifecycle.TERMINAL_STATES,
        "send_blocked": state in lifecycle.BLOCKING_STATES,
        "outbound_evidence": state == lifecycle.PROVIDER_CONFIRMED,
        "confidence": "fixture",
        "consistency": "matched",
    }
    return lifecycle_data, {"draft": draft or _draft()}, []


@pytest.mark.parametrize(
    ("state", "code"),
    [
        (lifecycle.PROVIDER_CONFIRMED, "DRAFT_ALREADY_SENT"),
        (lifecycle.BACKEND_CONFIRMED, "DRAFT_ALREADY_SENT"),
        (lifecycle.DISCARDED, "DRAFT_DISCARDED"),
        (lifecycle.SCHEDULED, "SEND_ALREADY_PENDING"),
        (lifecycle.REQUESTED, "SEND_ALREADY_PENDING"),
        (lifecycle.PENDING_UNDO, "SEND_ALREADY_PENDING"),
        (lifecycle.FAILED, "TERMINAL_SEND_JOB"),
        (lifecycle.ABORTED, "TERMINAL_SEND_JOB"),
        (lifecycle.INCONSISTENT, "LIFECYCLE_INCONSISTENT"),
    ],
)
def test_validate_and_execute_block_non_active_lifecycle_before_transport(state, code):
    with patch("superhuman_mail.send.lifecycle.observe", return_value=_observed(state)):
        with patch("superhuman_mail.send.urllib.request.urlopen") as urlopen:
            validated = send.validate(THREAD, DRAFT)
            executed = send.execute(THREAD, DRAFT, account=ACCOUNT["email"])
    assert validated["status"] == "failed"
    assert validated["errors"][0]["code"] == code
    assert executed["status"] == "failed"
    assert executed["errors"][0]["code"] == code
    urlopen.assert_not_called()


@pytest.mark.parametrize(
    ("draft", "code"),
    [
        (_draft(to=[], cc=[], bcc=[]), "RECIPIENTS_REQUIRED"),
        (_draft(to=[{"email": "not an address"}]), "INVALID_RECIPIENT"),
        (_draft(body="<div><br></div>"), "BODY_REQUIRED"),
        (_draft(**{"from": {"email": "wrong@example.test"}}), "FROM_ACCOUNT_MISMATCH"),
    ],
)
def test_invalid_envelope_or_content_is_blocked_inside_execute(draft, code):
    with patch("superhuman_mail.send.lifecycle.observe", return_value=_observed(draft=draft)):
        with patch("superhuman_mail.send.urllib.request.urlopen") as urlopen:
            result = send.execute(THREAD, DRAFT, account=ACCOUNT["email"])
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == code
    urlopen.assert_not_called()


def test_empty_subject_requires_explicit_exact_attestation_policy():
    with patch("superhuman_mail.send.lifecycle.observe", return_value=_observed(draft=_draft(subject=""))):
        validated = send.validate(THREAD, DRAFT)
        executed = send.execute(THREAD, DRAFT, account=ACCOUNT["email"])
    assert validated["errors"][0]["code"] == "SUBJECT_REQUIRED"
    assert executed["errors"][0]["code"] == "ATTESTATION_REQUIRED"


def test_validate_is_truthful_about_metadata_only_preflight():
    with patch("superhuman_mail.send.lifecycle.observe", return_value=_observed()):
        result = send.validate(THREAD, DRAFT)
    assert result["status"] == "succeeded"
    assert result["data"]["sendable"] is True
    assert result["data"]["send_eligible"] is False
    assert result["data"]["render_attested"] is False
    assert result["data"]["lifecycle"]["state"] == lifecycle.ACTIVE


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b"{}"


def test_execute_requires_exact_attestation_before_transport():
    with patch("superhuman_mail.send.lifecycle.observe", return_value=_observed()):
        with patch("superhuman_mail.send.urllib.request.urlopen") as urlopen:
            result = send.execute(THREAD, DRAFT, account=ACCOUNT["email"])
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "ATTESTATION_REQUIRED"
    urlopen.assert_not_called()
