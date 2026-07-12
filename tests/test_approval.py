"""Externally issued approval receipt authority and exact-binding tests."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from superhuman_mail import approval

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
ISSUER = "pinet-slack-approval-fixture"
KEY_ID = "ed25519:fixture"
APPROVER = "slack:U-fixture"


def _attestation(**overrides):
    value = {
        "attestation_id": "sha256:attestation",
        "account": {"email": "owner@example.test", "provider_user_id": "provider-user-fixture"},
        "thread_id": "thread-fixture",
        "draft_id": "draft-fixture",
        "superhuman_id": "sid.fixture",
        "delay_seconds": 20,
        "fingerprint": {"exact": "sha256:fingerprint"},
        "outgoing_payload": {
            "from": {"email": "owner@example.test"},
            "to": [{"email": "recipient@example.test"}],
            "cc": [],
            "bcc": [],
            "subject": "Fixture",
            "html_body": "<p>Exact body</p>",
            "attachments": [],
            "scheduled_for": None,
            "superhuman_id": "sid.fixture",
        },
    }
    value.update(overrides)
    return value


def _roots(private_key, *, approvers=None):
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        ISSUER: {
            "key_id": KEY_ID,
            "public_key": base64.urlsafe_b64encode(public).decode().rstrip("="),
            "allowed_approvers": approvers or [APPROVER],
        }
    }


def _receipt(
    private_key,
    attestation=None,
    *,
    issued_at=NOW,
    expires_at=None,
    approver=APPROVER,
    binding=None,
    action=approval.ACTION,
    provider=approval.PROVIDER,
):
    attestation = attestation or _attestation()
    expires_at = expires_at or (issued_at + timedelta(minutes=3))
    body = {
        "schema": approval.SCHEMA,
        "issuer": ISSUER,
        "key_id": KEY_ID,
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "nonce": "nonce-fixture-0123456789",
        "approver": {
            "principal": approver,
            "approval_event_id": "slack-event-fixture",
        },
        "action": action,
        "provider": provider,
        "binding": binding or approval.binding_for_attestation(attestation),
    }
    receipt_id = approval.sha256(approval.canonical_bytes(body))
    signed = {**body, "receipt_id": receipt_id}
    signature = private_key.sign(approval.canonical_bytes(signed))
    return {
        **signed,
        "signature": "ed25519:" + base64.urlsafe_b64encode(signature).decode().rstrip("="),
    }


def test_valid_external_receipt_verifies_exact_binding():
    private = Ed25519PrivateKey.generate()
    attestation = _attestation()
    result = approval.verify(
        _receipt(private, attestation),
        attestation=attestation,
        roots=_roots(private),
        now=NOW + timedelta(seconds=1),
    )
    assert result["authority"] == approval.AUTHORITY
    assert result["verified"] is True
    assert result["issuer"] == ISSUER
    assert result["approver"] == APPROVER
    assert result["binding"] == approval.binding_for_attestation(attestation)


def test_forged_signature_fails_against_pinned_public_key():
    trusted = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    with pytest.raises(approval.ApprovalError) as caught:
        approval.verify(
            _receipt(attacker),
            attestation=_attestation(),
            roots=_roots(trusted),
            now=NOW + timedelta(seconds=1),
        )
    assert caught.value.code == "APPROVAL_RECEIPT_FORGED"


@pytest.mark.parametrize(
    "attestation",
    [
        _attestation(thread_id="wrong-thread"),
        _attestation(draft_id="wrong-draft"),
        _attestation(delay_seconds=99),
        _attestation(superhuman_id="wrong-sid"),
        _attestation(account={"email": "other@example.test", "provider_user_id": "other-user"}),
        _attestation(outgoing_payload={**_attestation()["outgoing_payload"], "to": [{"email": "other@example.test"}]}),
        _attestation(outgoing_payload={**_attestation()["outgoing_payload"], "html_body": "<p>changed</p>"}),
    ],
)
def test_signed_receipt_cannot_authorize_mismatched_exact_send(attestation):
    private = Ed25519PrivateKey.generate()
    receipt = _receipt(private, _attestation())
    with pytest.raises(approval.ApprovalError) as caught:
        approval.verify(
            receipt,
            attestation=attestation,
            roots=_roots(private),
            now=NOW + timedelta(seconds=1),
        )
    assert caught.value.code == "APPROVAL_BINDING_MISMATCH"


def test_tampered_signed_field_fails_canonical_receipt_identity():
    private = Ed25519PrivateKey.generate()
    receipt = _receipt(private)
    receipt["binding"] = {**receipt["binding"], "delay_seconds": 99}
    with pytest.raises(approval.ApprovalError) as caught:
        approval.verify(receipt, attestation=_attestation(), roots=_roots(private), now=NOW)
    assert caught.value.code == "APPROVAL_RECEIPT_TAMPERED"


@pytest.mark.parametrize(
    ("issued_at", "expires_at", "code"),
    [
        (NOW - timedelta(minutes=4), NOW - timedelta(seconds=1), "APPROVAL_RECEIPT_EXPIRED"),
        (NOW + timedelta(minutes=1), NOW + timedelta(minutes=2), "APPROVAL_RECEIPT_NOT_YET_VALID"),
        (NOW, NOW + timedelta(minutes=6), "APPROVAL_RECEIPT_INVALID"),
    ],
)
def test_receipt_lifetime_fails_closed(issued_at, expires_at, code):
    private = Ed25519PrivateKey.generate()
    with pytest.raises(approval.ApprovalError) as caught:
        approval.verify(
            _receipt(private, issued_at=issued_at, expires_at=expires_at),
            attestation=_attestation(),
            roots=_roots(private),
            now=NOW,
        )
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"action": "other.send"}, "APPROVAL_ACTION_MISMATCH"),
        ({"provider": "other"}, "APPROVAL_ACTION_MISMATCH"),
    ],
)
def test_signed_receipt_for_other_action_or_provider_is_rejected(overrides, code):
    private = Ed25519PrivateKey.generate()
    with pytest.raises(approval.ApprovalError) as caught:
        approval.verify(
            _receipt(private, **overrides),
            attestation=_attestation(),
            roots=_roots(private),
            now=NOW,
        )
    assert caught.value.code == code


def test_unknown_or_wrong_key_id_is_rejected_before_signature_trust():
    private = Ed25519PrivateKey.generate()
    roots = _roots(private)
    roots[ISSUER]["key_id"] = "different-key"
    with pytest.raises(approval.ApprovalError) as caught:
        approval.verify(
            _receipt(private),
            attestation=_attestation(),
            roots=roots,
            now=NOW,
        )
    assert caught.value.code == "APPROVAL_ISSUER_UNTRUSTED"


def test_malformed_allowed_approver_configuration_fails_closed():
    private = Ed25519PrivateKey.generate()
    roots = _roots(private)
    roots[ISSUER]["allowed_approvers"] = APPROVER
    with pytest.raises(approval.ApprovalError) as caught:
        approval.verify(
            _receipt(private),
            attestation=_attestation(),
            roots=roots,
            now=NOW,
        )
    assert caught.value.code == "APPROVAL_TRUST_UNAVAILABLE"


def test_bounded_root_list_selects_exact_issuer_and_key_during_rotation():
    private = Ed25519PrivateKey.generate()
    active = {"issuer": ISSUER, **_roots(private)[ISSUER]}
    old = {"issuer": ISSUER, "key_id": "old-key", "public_key": active["public_key"], "allowed_approvers": [APPROVER]}
    result = approval.verify(
        _receipt(private), attestation=_attestation(), roots=[old, active], now=NOW + timedelta(seconds=1),
    )
    assert result["key_id"] == KEY_ID


def test_signed_but_unauthorized_approver_is_rejected():
    private = Ed25519PrivateKey.generate()
    with pytest.raises(approval.ApprovalError) as caught:
        approval.verify(
            _receipt(private, approver="slack:attacker"),
            attestation=_attestation(),
            roots=_roots(private),
            now=NOW,
        )
    assert caught.value.code == "APPROVER_UNAUTHORIZED"


def test_no_pinned_external_issuer_disables_strict_send(monkeypatch):
    monkeypatch.setattr(approval, "BUILTIN_TRUST_ROOTS", {})
    monkeypatch.setattr(approval, "TRUST_STORE_PATH", Path("/nonexistent/trust.json"))
    with pytest.raises(approval.ApprovalError) as caught:
        approval.trusted_issuers()
    assert caught.value.code == "APPROVAL_TRUST_UNAVAILABLE"


def test_user_writable_trust_store_path_is_rejected(monkeypatch, tmp_path):
    trust = tmp_path / "approval-trust-v1.json"
    trust.write_text('{"schema":"shm-approval-trust/v1","issuers":{}}')
    monkeypatch.setattr(approval, "TRUST_STORE_PATH", trust)
    with pytest.raises(approval.ApprovalError) as caught:
        approval._read_system_trust_store()
    assert caught.value.code == "APPROVAL_TRUST_UNSAFE"


def test_caller_environment_cannot_install_approval_root(monkeypatch):
    monkeypatch.setenv("SHM_APPROVAL_PUBLIC_KEY", "attacker-controlled")
    monkeypatch.setattr(approval, "BUILTIN_TRUST_ROOTS", {})
    monkeypatch.setattr(approval, "TRUST_STORE_PATH", Path("/nonexistent/trust.json"))
    with pytest.raises(approval.ApprovalError) as caught:
        approval.trusted_issuers()
    assert caught.value.code == "APPROVAL_TRUST_UNAVAILABLE"


def test_safe_verify_surface_defers_consumption_state_to_canonical_executor():
    attestation = _attestation()
    verified = {
        "receipt_id": "sha256:" + "a" * 64,
        "receipt_digest": "sha256:" + "b" * 64,
        "issuer": ISSUER,
        "key_id": KEY_ID,
        "approver": APPROVER,
        "approval_event_id": "event-fixture",
        "issued_at": "2026-07-10T12:00:00Z",
        "expires_at": "2026-07-10T12:03:00Z",
        "expired": False,
        "binding": approval.binding_for_attestation(attestation),
    }

    with patch("superhuman_mail.attestation.load", return_value=attestation):
        with patch("superhuman_mail.attestation.verify"):
            with patch("superhuman_mail.approval.load", return_value={"receipt": "fixture"}):
                with patch("superhuman_mail.approval.verify", return_value=verified):
                    result = approval.show_safe(
                        "receipt.json",
                        attestation_reference="attestation-fixture",
                    )
    assert result["authority"] == approval.AUTHORITY
    assert result["verified"] is True
    assert result["usable_for_executor_submission"] is True
    assert result["consumption_state"] == "query_canonical_executor"
    assert result["unattended_send_eligible"] is False
    assert result["trusted_executor_required"] is True
    assert "event-fixture" not in str(result)


def test_published_json_schema_binding_matches_core_contract():
    schema = json.loads((Path(__file__).parents[1] / "docs" / "approval-receipt-v1.schema.json").read_text())
    required = set(schema["properties"]["binding"]["required"])
    assert required == set(approval.binding_for_attestation(_attestation()))


def test_receipt_binding_contains_hashes_not_mail_content():
    binding = approval.binding_for_attestation(_attestation())
    serialized = str(binding)
    assert "recipient@example.test" not in serialized
    assert "Exact body" not in serialized
    assert binding["action"] == approval.ACTION
    assert binding["provider"] == approval.PROVIDER
    assert binding["outgoing_payload_sha256"].startswith("sha256:")
