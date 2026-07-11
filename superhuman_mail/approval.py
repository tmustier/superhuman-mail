"""Externally issued, exact-send approval receipt verification.

This module deliberately contains no receipt-minting path. Production trust roots
are either compiled into the package or installed in a root-owned, non-writable
system trust store. The unattended worker receives public verification material
only; the issuer's Ed25519 private key must remain in the external approval broker.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature  # pyright: ignore[reportMissingImports]
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # pyright: ignore[reportMissingImports]
    Ed25519PublicKey,
)

from ._state import private_file, state_dir
from .attestation import canonical_bytes, sha256

SCHEMA = "shm-approval-receipt/v1"
TRUST_SCHEMA = "shm-approval-trust/v1"
AUTHORITY = "external_ed25519_receipt_v1"
ACTION = "superhuman.send"
PROVIDER = "superhuman"
MAX_TTL = timedelta(minutes=5)
MAX_CLOCK_SKEW = timedelta(seconds=30)
TRUST_STORE_PATH = Path("/Library/Application Support/superhuman-mail/approval-trust-v1.json")

# Release-pinned roots may be added here after the external issuer has generated
# its keypair. Never add a private key or a caller-controlled environment path.
BUILTIN_TRUST_ROOTS: dict[str, dict[str, Any]] = {}


class ApprovalError(RuntimeError):
    """A receipt is absent, forged, stale, replayed, or bound elsewhere."""

    def __init__(self, code: str, hint: str) -> None:
        super().__init__(hint)
        self.code = code
        self.hint = hint


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hash_text(value: Any) -> str:
    return sha256(str(value or "").encode())


def _b64decode(value: str) -> bytes:
    raw = value.encode()
    raw += b"=" * (-len(raw) % 4)
    try:
        return base64.b64decode(raw, altchars=b"-_", validate=True)
    except Exception as exc:
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt contains invalid base64") from exc


def binding_for_attestation(record: dict[str, Any]) -> dict[str, Any]:
    """Build the content-free exact-send binding an external issuer must sign."""
    payload = record.get("outgoing_payload") or {}
    envelope = {
        "from": payload.get("from"),
        "to": payload.get("to") or [],
        "cc": payload.get("cc") or [],
        "bcc": payload.get("bcc") or [],
    }
    renderer = record.get("renderer") or {}
    screenshot_hashes = [str(item.get("sha256") or "") for item in (record.get("screenshots") or [])]
    return {
        "action": ACTION,
        "provider": PROVIDER,
        "account_provider_user_id_sha256": _hash_text((record.get("account") or {}).get("provider_user_id")),
        "account_email_sha256": _hash_text(str((record.get("account") or {}).get("email") or "").lower()),
        "thread_id_sha256": _hash_text(record.get("thread_id")),
        "draft_id_sha256": _hash_text(record.get("draft_id")),
        "attestation_id": str(record.get("attestation_id") or ""),
        "outgoing_fingerprint": str((record.get("fingerprint") or {}).get("exact") or ""),
        "outgoing_payload_sha256": sha256(canonical_bytes(payload)),
        "recipient_envelope_sha256": sha256(canonical_bytes(envelope)),
        "renderer_build_sha256": sha256(canonical_bytes({
            "adapter_version": renderer.get("adapter_version"),
            "app_version": renderer.get("app_version"),
            "web_version": renderer.get("web_version"),
        })),
        "screenshot_set_sha256": sha256(canonical_bytes(screenshot_hashes)),
        "send_identity_sha256": _hash_text(record.get("superhuman_id")),
        "delay_seconds": int(record.get("delay_seconds", -1)),
        "scheduled_for_sha256": _hash_text(payload.get("scheduled_for")),
    }


def _receipt_content(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in {"receipt_id", "signature"}}


def _signed_content(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "signature"}


def _validate_structure(record: dict[str, Any]) -> None:
    expected_fields = {
        "schema", "receipt_id", "issuer", "key_id", "issued_at", "expires_at",
        "nonce", "approver", "action", "provider", "binding", "signature",
    }
    if set(record) != expected_fields:
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt fields do not match schema v1")
    if record.get("schema") != SCHEMA:
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Unsupported approval receipt schema")
    for key in ("receipt_id", "issuer", "key_id", "issued_at", "expires_at", "nonce", "signature"):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ApprovalError("APPROVAL_RECEIPT_INVALID", f"Approval receipt is missing {key}")
    if not record["receipt_id"].startswith("sha256:") or len(record["receipt_id"]) != 71:
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt ID is malformed")
    if not 16 <= len(record["nonce"]) <= 256:
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt nonce is malformed")
    if record.get("action") != ACTION or record.get("provider") != PROVIDER:
        raise ApprovalError("APPROVAL_ACTION_MISMATCH", "Approval receipt is for a different action or provider")
    if not isinstance(record.get("binding"), dict):
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt binding is malformed")
    approver = record.get("approver")
    if not isinstance(approver, dict) or set(approver) != {"principal", "approval_event_id"} or not all(
        isinstance(approver.get(key), str) and approver.get(key)
        for key in ("principal", "approval_event_id")
    ):
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt approver evidence is malformed")


def _read_system_trust_store() -> list[dict[str, Any]]:
    if not TRUST_STORE_PATH.exists():
        return []
    try:
        current = TRUST_STORE_PATH.parent
        while current != current.parent:
            metadata = current.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise ApprovalError(
                    "APPROVAL_TRUST_UNSAFE",
                    "Approval trust-store directory chain must be root-owned and non-writable",
                )
            current = current.parent
        descriptor = os.open(TRUST_STORE_PATH, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise ApprovalError(
                    "APPROVAL_TRUST_UNSAFE",
                    "Approval trust store must be root-owned and not writable by group/others",
                )
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                descriptor = -1
                value = json.load(handle)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except ApprovalError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalError("APPROVAL_TRUST_UNAVAILABLE", "Cannot read the system approval trust store") from exc
    if not isinstance(value, dict) or set(value) != {"schema", "roots"} or value.get("schema") != TRUST_SCHEMA:
        raise ApprovalError("APPROVAL_TRUST_UNAVAILABLE", "System approval trust store is malformed")
    roots = value.get("roots")
    if not isinstance(roots, list) or not 1 <= len(roots) <= 2:
        raise ApprovalError("APPROVAL_TRUST_UNAVAILABLE", "System approval trust store must contain one or two roots")
    expected = {"issuer", "key_id", "public_key", "allowed_approvers"}
    identities: set[tuple[str, str]] = set()
    for root in roots:
        if not isinstance(root, dict) or set(root) != expected:
            raise ApprovalError("APPROVAL_TRUST_UNAVAILABLE", "System approval root fields are malformed")
        identity = (str(root.get("issuer") or ""), str(root.get("key_id") or ""))
        if not all(identity) or identity in identities:
            raise ApprovalError("APPROVAL_TRUST_UNAVAILABLE", "System approval roots contain an invalid duplicate")
        identities.add(identity)
    return [dict(root) for root in roots]


def trusted_issuers() -> list[dict[str, Any]]:
    roots = [
        {"issuer": issuer, **dict(configuration)}
        for issuer, configuration in BUILTIN_TRUST_ROOTS.items()
    ]
    for configuration in _read_system_trust_store():
        identity = (configuration["issuer"], configuration["key_id"])
        existing = next((root for root in roots if (root.get("issuer"), root.get("key_id")) == identity), None)
        if existing and existing != configuration:
            raise ApprovalError("APPROVAL_TRUST_CONFLICT", f"Conflicting approval trust root for {identity[0]}/{identity[1]}")
        if not existing:
            roots.append(configuration)
    if len(roots) > 2:
        raise ApprovalError("APPROVAL_TRUST_UNAVAILABLE", "Approval trust accepts at most two roots")
    if not roots:
        raise ApprovalError(
            "APPROVAL_TRUST_UNAVAILABLE",
            "No externally isolated approval issuer is pinned; strict send is disabled",
        )
    return roots


def load(reference: str | Path) -> dict[str, Any]:
    try:
        path = Path(reference).expanduser()
        if not path.is_file():
            raise ApprovalError("APPROVAL_RECEIPT_NOT_FOUND", "Approval receipt file was not found")
        if path.stat().st_size > 64 * 1024:
            raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt exceeds 64 KiB")
        record = json.loads(path.read_text())
    except ApprovalError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt is not valid JSON") from exc
    if not isinstance(record, dict):
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt must be a JSON object")
    return record


def verify(
    record: dict[str, Any],
    *,
    attestation: dict[str, Any],
    roots: dict[str, dict[str, Any]] | list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    require_unexpired: bool = True,
) -> dict[str, Any]:
    """Verify external authority, lifetime, exact binding, and canonical identity."""
    _validate_structure(record)
    expected_receipt_id = sha256(canonical_bytes(_receipt_content(record)))
    if not hmac.compare_digest(record["receipt_id"], expected_receipt_id):
        raise ApprovalError("APPROVAL_RECEIPT_TAMPERED", "Approval receipt ID does not match canonical content")

    roots = roots if roots is not None else trusted_issuers()
    if isinstance(roots, dict):
        issuer = roots.get(record["issuer"])
        if not isinstance(issuer, dict) or issuer.get("key_id") != record["key_id"]:
            raise ApprovalError("APPROVAL_ISSUER_UNTRUSTED", "Approval receipt issuer/key is not pinned")
    else:
        issuer = next((root for root in roots if root.get("issuer") == record["issuer"] and root.get("key_id") == record["key_id"]), None)
        if not isinstance(issuer, dict):
            raise ApprovalError("APPROVAL_ISSUER_UNTRUSTED", "Approval receipt issuer/key is not pinned")
    allowed_approvers = issuer.get("allowed_approvers") or []
    if (
        not isinstance(issuer.get("public_key"), str)
        or not isinstance(allowed_approvers, list)
        or not allowed_approvers
        or any(not isinstance(item, str) or not item for item in allowed_approvers)
    ):
        raise ApprovalError("APPROVAL_TRUST_UNAVAILABLE", "Pinned approval issuer configuration is malformed")
    principal = record["approver"]["principal"]
    if principal not in allowed_approvers:
        raise ApprovalError("APPROVER_UNAUTHORIZED", "Approval receipt approver is not authorized")

    public_key_bytes = _b64decode(str(issuer.get("public_key") or ""))
    if len(public_key_bytes) != 32:
        raise ApprovalError("APPROVAL_TRUST_UNAVAILABLE", "Pinned Ed25519 public key is malformed")
    signature = str(record["signature"])
    if not signature.startswith("ed25519:"):
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt signature algorithm is unsupported")
    signature_bytes = _b64decode(signature.split(":", 1)[1])
    if len(signature_bytes) != 64:
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Ed25519 approval signature is malformed")
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature_bytes,
            canonical_bytes(_signed_content(record)),
        )
    except InvalidSignature as exc:
        raise ApprovalError("APPROVAL_RECEIPT_FORGED", "Approval receipt signature is invalid") from exc

    try:
        issued_at = _parse_iso(record["issued_at"])
        expires_at = _parse_iso(record["expires_at"])
    except (TypeError, ValueError) as exc:
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt lifetime is malformed") from exc
    current = now or _now()
    if issued_at > current + MAX_CLOCK_SKEW:
        raise ApprovalError("APPROVAL_RECEIPT_NOT_YET_VALID", "Approval receipt issue time is in the future")
    expired = expires_at <= current
    if require_unexpired and expired:
        raise ApprovalError("APPROVAL_RECEIPT_EXPIRED", "Approval receipt has expired")
    if expires_at <= issued_at or expires_at - issued_at > MAX_TTL:
        raise ApprovalError("APPROVAL_RECEIPT_INVALID", "Approval receipt lifetime exceeds the five-minute maximum")

    expected_binding = binding_for_attestation(attestation)
    if not hmac.compare_digest(canonical_bytes(record["binding"]), canonical_bytes(expected_binding)):
        raise ApprovalError("APPROVAL_BINDING_MISMATCH", "Approval receipt does not bind this exact send")

    return {
        "authority": AUTHORITY,
        "verified": True,
        "receipt_id": record["receipt_id"],
        "receipt_digest": sha256(canonical_bytes(record)),
        "issuer": record["issuer"],
        "key_id": record["key_id"],
        "approver": principal,
        "approval_event_id": record["approver"]["approval_event_id"],
        "issued_at": record["issued_at"],
        "expires_at": record["expires_at"],
        "expired": expired,
        "binding": expected_binding,
    }


def load_and_verify(reference: str | Path, *, attestation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = load(reference)
    verified = verify(receipt, attestation=attestation)
    archive(receipt)
    return receipt, verified


def show_safe(
    reference: str | Path,
    *,
    attestation_reference: str | Path,
    journal: Any | None = None,
) -> dict[str, Any]:
    """Return content-free typed verification and replay-consumption state."""
    from . import attestation as _attestation
    attestation = _attestation.load(attestation_reference)
    _attestation.verify(attestation)
    receipt = load(reference)
    verified = verify(receipt, attestation=attestation, require_unexpired=False)
    del journal
    return {
        "authority": AUTHORITY,
        "verified": True,
        "usable_for_executor_submission": not verified["expired"],
        "unattended_send_eligible": False,
        "trusted_executor_required": True,
        "expired": verified["expired"],
        "consumption_state": "query_canonical_executor",
        "receipt_id": verified["receipt_id"],
        "receipt_digest": verified["receipt_digest"],
        "issuer": verified["issuer"],
        "key_id": verified["key_id"],
        "approver": verified["approver"],
        "approval_event_id_sha256": _hash_text(verified["approval_event_id"]),
        "issued_at": verified["issued_at"],
        "expires_at": verified["expires_at"],
        "attestation_id": attestation["attestation_id"],
        "outgoing_fingerprint": (attestation.get("fingerprint") or {}).get("exact"),
        "binding": verified["binding"],
    }


def archive(record: dict[str, Any]) -> Path:
    directory = state_dir() / "approval-receipts"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    receipt_id = str(record["receipt_id"]).removeprefix("sha256:")
    path = directory / f"{receipt_id}.json"
    encoded = canonical_bytes(record) + b"\n"
    if path.exists() and path.read_bytes() != encoded:
        raise ApprovalError("APPROVAL_RECEIPT_TAMPERED", "Archived receipt ID already has different content")
    path.write_bytes(encoded)
    private_file(path)
    return path
