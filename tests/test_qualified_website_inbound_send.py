"""Policy-scoped qualified website-inbound send tests; transport is always fake."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from superhuman_mail import attempts, lifecycle, send

THREAD = "draftthread_fixture"
DRAFT = "draft_fixture"
ACCOUNT = {"email": "owner@example.test", "provider_user_id": "user-fixture"}
LEAD = "lead@example.test"
QUALIFICATION_REF = "website-inbounds:webin-0123abcd"


def _draft(**overrides):
    value = {
        "id": DRAFT,
        "threadId": THREAD,
        "action": "compose",
        "from": {"email": ACCOUNT["email"]},
        "to": [{"email": LEAD}],
        "cc": [{"email": "colleague@example.test"}],
        "bcc": [],
        "subject": "Nexcade",
        "body": "<div>Hello</div>",
        "attachments": [],
        "rfc822Id": "<fixture@example.test>",
    }
    value.update(overrides)
    return value


def _observed(state=lifecycle.ACTIVE, *, draft=None, provider_message_id=None):
    provider = None
    if provider_message_id:
        provider = {"id": provider_message_id, "labels": ["SENT"]}
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
        "timestamps": {"provider_message_at": "2026-07-13T12:00:00Z" if provider else None},
        "provider_message": provider,
        "send_job": {},
    }
    return lifecycle_data, {"draft": draft or _draft()}, []


def _execute(journal: attempts.AttemptJournal, *, wait=0):
    return send.execute_qualified_website_inbound(
        THREAD,
        DRAFT,
        account=ACCOUNT["email"],
        lead_email=LEAD,
        qualification_ref=QUALIFICATION_REF,
        wait=wait,
        journal=journal,
    )


def test_provider_confirmed_send_posts_once_and_replays_from_tombstone(tmp_path: Path):
    journal = attempts.AttemptJournal(tmp_path / "attempts.sqlite3")
    observations = [
        _observed(),
        _observed(),
        _observed(lifecycle.PROVIDER_CONFIRMED, provider_message_id="provider-message-1"),
    ]
    with (
        patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])),
        patch("superhuman_mail.send.lifecycle.observe", side_effect=observations),
        patch("superhuman_mail.send._post_exact_payload") as post,
    ):
        result = _execute(journal)

    assert result["status"] == "succeeded"
    assert result["data"]["state"] == lifecycle.PROVIDER_CONFIRMED
    assert result["data"]["provider_confirmed"] is True
    assert result["data"]["sent"] is True
    assert result["data"]["automation_policy"] == send.QUALIFIED_WEBSITE_INBOUND_POLICY
    assert result["data"]["approval_authority"] == "policy_scoped_automation"
    assert result["data"]["unattended_send_eligible"] is True
    assert result["data"]["trusted_executor_required"] is False
    assert result["data"]["attestation_id"] is None
    post.assert_called_once()

    with (
        patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])),
        patch("superhuman_mail.send.lifecycle.observe") as observe,
        patch("superhuman_mail.send._post_exact_payload") as replay_post,
    ):
        replay = _execute(journal)
    assert replay["data"]["provider_confirmed"] is True
    observe.assert_not_called()
    replay_post.assert_not_called()


def test_terminal_attempt_retains_body_free_lifecycle_for_source_checkpoint(tmp_path: Path):
    journal = attempts.AttemptJournal(tmp_path / "attempts.sqlite3")
    with (
        patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])),
        patch(
            "superhuman_mail.send.lifecycle.observe",
            side_effect=[_observed(), _observed(), _observed(lifecycle.FAILED)],
        ),
        patch("superhuman_mail.send._post_exact_payload") as post,
    ):
        result = _execute(journal)
    assert result["status"] == "failed"
    assert result["data"]["state"] == lifecycle.FAILED
    assert result["data"]["post_claimed"] is True
    assert result["data"]["automation_policy"] == send.QUALIFIED_WEBSITE_INBOUND_POLICY
    post.assert_called_once()


def test_pending_attempt_reconciles_without_second_post(tmp_path: Path):
    journal = attempts.AttemptJournal(tmp_path / "attempts.sqlite3")
    with (
        patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])),
        patch("superhuman_mail.send.lifecycle.observe", side_effect=[_observed(), _observed(), _observed()]),
        patch("superhuman_mail.send._post_exact_payload") as post,
    ):
        pending = _execute(journal)
    assert pending["status"] == "succeeded"
    assert pending["data"]["state"] == "unknown"
    assert pending["data"]["post_claimed"] is True
    assert pending["data"]["provider_confirmed"] is False
    post.assert_called_once()

    with (
        patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])),
        patch(
            "superhuman_mail.send.lifecycle.observe",
            return_value=_observed(lifecycle.PROVIDER_CONFIRMED, provider_message_id="provider-message-2"),
        ),
        patch("superhuman_mail.send._post_exact_payload") as retry_post,
    ):
        confirmed = _execute(journal)
    assert confirmed["data"]["provider_confirmed"] is True
    retry_post.assert_not_called()


@pytest.mark.parametrize(
    ("draft", "code"),
    [
        (_draft(action="reply"), "WEBSITE_INBOUND_COMPOSE_REQUIRED"),
        (_draft(to=[{"email": "other@example.test"}]), "WEBSITE_INBOUND_TARGET_MISMATCH"),
        (_draft(to=[{"email": LEAD}, {"email": "other@example.test"}]), "WEBSITE_INBOUND_TARGET_MISMATCH"),
        (_draft(bcc=[{"email": "hidden@example.test"}]), "WEBSITE_INBOUND_BCC_FORBIDDEN"),
        (_draft(attachments=[{"uuid": "file-1"}]), "WEBSITE_INBOUND_ATTACHMENTS_FORBIDDEN"),
        (_draft(scheduledFor="2026-07-14T09:00:00Z"), "WEBSITE_INBOUND_SCHEDULE_FORBIDDEN"),
    ],
)
def test_policy_rejects_out_of_scope_draft_shapes_before_transport(tmp_path: Path, draft, code):
    journal = attempts.AttemptJournal(tmp_path / "attempts.sqlite3")
    with (
        patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])),
        patch("superhuman_mail.send.lifecycle.observe", return_value=_observed(draft=draft)),
        patch("superhuman_mail.send._post_exact_payload") as post,
    ):
        result = _execute(journal)
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == code
    post.assert_not_called()


def test_policy_requires_canonical_website_qualification_reference(tmp_path: Path):
    journal = attempts.AttemptJournal(tmp_path / "attempts.sqlite3")
    with (
        patch("superhuman_mail.send.lifecycle.resolve_account") as resolve,
        patch("superhuman_mail.send._post_exact_payload") as post,
    ):
        result = send.execute_qualified_website_inbound(
            THREAD,
            DRAFT,
            account=ACCOUNT["email"],
            lead_email=LEAD,
            qualification_ref="manual:approved",
            wait=0,
            journal=journal,
        )
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "QUALIFICATION_REFERENCE_INVALID"
    resolve.assert_not_called()
    post.assert_not_called()


def test_attempt_binding_rejects_changed_lead_on_retry(tmp_path: Path):
    journal = attempts.AttemptJournal(tmp_path / "attempts.sqlite3")
    with (
        patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])),
        patch("superhuman_mail.send.lifecycle.observe", side_effect=[_observed(), _observed(), _observed()]),
        patch("superhuman_mail.send._post_exact_payload"),
    ):
        pending = _execute(journal)
    assert pending["data"]["post_claimed"] is True

    with patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
        changed = send.execute_qualified_website_inbound(
            THREAD,
            DRAFT,
            account=ACCOUNT["email"],
            lead_email="changed@example.test",
            qualification_ref=QUALIFICATION_REF,
            wait=0,
            journal=journal,
        )
    assert changed["status"] == "failed"
    assert changed["errors"][0]["code"] == "ATTEMPT_CONFLICT"
