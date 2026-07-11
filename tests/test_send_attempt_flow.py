"""Canonical authority routing tests; no local/provider transport is exercised."""
from __future__ import annotations

from unittest.mock import patch

from superhuman_mail import authority_client, send

THREAD = "thread_fixture"
DRAFT = "draft_fixture"
ACCOUNT = "owner@example.test"
ATTESTATION_ID = "sha256:" + "a" * 64


def _record(tmp_path):
    screenshot = tmp_path / "compose.png"
    screenshot.write_bytes(b"synthetic screenshot")
    return {
        "attestation_id": ATTESTATION_ID,
        "account": {"email": ACCOUNT, "provider_user_id": "provider-fixture"},
        "thread_id": THREAD,
        "draft_id": DRAFT,
        "delay_seconds": 20,
        "screenshots": [{"path": str(screenshot), "sha256": authority_client.attestation.sha256(screenshot.read_bytes())}],
    }


def test_send_execute_routes_import_then_execute_to_canonical_authority(tmp_path):
    record = _record(tmp_path)
    receipt = {"receipt_id": "sha256:" + "b" * 64, "issuer": "fixture-issuer", "key_id": "fixture-key"}
    calls = []

    def request(path, payload, *, timeout):
        calls.append((path, payload, timeout))
        if path == "/v1/import-attestation":
            return {"imported": True, "attestation_id": ATTESTATION_ID}
        return {"state": "provider_confirmed", "receiptId": receipt["receipt_id"]}

    with (
        patch("superhuman_mail.authority_client.attestation.load", return_value=record),
        patch("superhuman_mail.authority_client.attestation.verify"),
        patch("superhuman_mail.authority_client.approval.load", return_value=receipt),
        patch("superhuman_mail.authority_client.approval.verify"),
        patch("superhuman_mail.authority_client._request", side_effect=request),
        patch("superhuman_mail.send._post_exact_payload") as raw_post,
    ):
        result = send.execute(
            THREAD,
            DRAFT,
            account=ACCOUNT,
            attestation=ATTESTATION_ID,
            approval_receipt="receipt.json",
            wait=0,
        )

    assert result["status"] == "succeeded"
    assert result["data"]["sent"] is True
    assert [item[0] for item in calls] == ["/v1/import-attestation", "/v1/execute"]
    assert calls[1][1]["execution"] == {
        "account": ACCOUNT,
        "thread_id": THREAD,
        "draft_id": DRAFT,
        "attestation_id": ATTESTATION_ID,
    }
    screenshot = calls[0][1]["attestation_bundle"]["screenshots"][0]
    assert screenshot["sha256"] == record["screenshots"][0]["sha256"]
    assert screenshot["data_base64"]
    raw_post.assert_not_called()


def test_send_execute_has_no_local_journal_or_raw_transport_fallback(tmp_path):
    record = _record(tmp_path)
    with (
        patch("superhuman_mail.authority_client.attestation.load", return_value=record),
        patch("superhuman_mail.authority_client.attestation.verify"),
        patch("superhuman_mail.authority_client.approval.load", return_value={}),
        patch("superhuman_mail.authority_client.approval.verify"),
        patch("superhuman_mail.authority_client._request", side_effect=authority_client.AuthorityClientError("SEND_EXECUTOR_UNAVAILABLE", "offline")),
        patch("superhuman_mail.send._post_exact_payload") as raw_post,
    ):
        result = send.execute(
            THREAD,
            DRAFT,
            account=ACCOUNT,
            attestation=ATTESTATION_ID,
            approval_receipt="receipt.json",
        )
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "SEND_EXECUTOR_UNAVAILABLE"
    raw_post.assert_not_called()


def test_missing_exact_authority_inputs_fail_before_socket():
    with patch("superhuman_mail.authority_client._request") as request:
        result = send.execute(THREAD, DRAFT, account=ACCOUNT)
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "ATTESTATION_REQUIRED"
    request.assert_not_called()
