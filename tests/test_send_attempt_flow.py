"""Canonical authority routing tests; no local/provider transport is exercised."""
from __future__ import annotations

from unittest.mock import patch

from superhuman_mail import authority_client, send

THREAD = "thread_fixture"
DRAFT = "draft_fixture"
ACCOUNT = "owner@example.test"
ATTESTATION_ID = "sha256:" + "a" * 64


def _receipt():
    return {
        "receipt_id": "sha256:" + "b" * 64,
        "issuer": "fixture-issuer",
        "key_id": "fixture-key",
        "binding": {
            "attestation_id": ATTESTATION_ID,
            "account_email_sha256": authority_client.attestation.sha256(ACCOUNT),
            "thread_id_sha256": authority_client.attestation.sha256(THREAD),
            "draft_id_sha256": authority_client.attestation.sha256(DRAFT),
            "delay_seconds": 20,
        },
    }


def test_send_execute_routes_only_to_canonical_authority():
    receipt = _receipt(); calls = []

    def request(path, payload, *, timeout):
        calls.append((path, payload, timeout))
        return {"state": "provider_confirmed", "receiptId": receipt["receipt_id"]}

    with (
        patch("superhuman_mail.authority_client.approval.load", return_value=receipt),
        patch("superhuman_mail.authority_client.approval._validate_structure"),
        patch("superhuman_mail.authority_client._request", side_effect=request),
        patch("superhuman_mail.send._post_exact_payload") as raw_post,
    ):
        result = send.execute(
            THREAD, DRAFT, account=ACCOUNT, attestation=ATTESTATION_ID,
            approval_receipt="receipt.json", wait=0,
        )

    assert result["status"] == "succeeded"
    assert result["data"]["sent"] is True
    assert [item[0] for item in calls] == ["/v1/execute"]
    assert calls[0][1]["execution"] == {
        "account": ACCOUNT, "thread_id": THREAD, "draft_id": DRAFT, "attestation_id": ATTESTATION_ID,
    }
    raw_post.assert_not_called()


def test_send_execute_has_no_local_journal_or_raw_transport_fallback():
    with (
        patch("superhuman_mail.authority_client.approval.load", return_value=_receipt()),
        patch("superhuman_mail.authority_client.approval._validate_structure"),
        patch("superhuman_mail.authority_client._request", side_effect=authority_client.AuthorityClientError("SEND_EXECUTOR_UNAVAILABLE", "offline")),
        patch("superhuman_mail.send._post_exact_payload") as raw_post,
    ):
        result = send.execute(
            THREAD, DRAFT, account=ACCOUNT, attestation=ATTESTATION_ID,
            approval_receipt="receipt.json",
        )
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "SEND_EXECUTOR_UNAVAILABLE"
    raw_post.assert_not_called()


def test_missing_receipt_fails_before_socket():
    with patch("superhuman_mail.authority_client._request") as request:
        result = send.execute(THREAD, DRAFT, account=ACCOUNT)
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "APPROVAL_RECEIPT_REQUIRED"
    request.assert_not_called()
