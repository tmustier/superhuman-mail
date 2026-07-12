"""Thin credential-free client for the one canonical send authority."""
from __future__ import annotations

import http.client
import json
import socket
from typing import Any

from . import approval, attestation
from ._envelope import error, fail, ok

EXECUTOR_SOCKET = "/var/run/superhuman-mail/send-executor/execute.sock"


class AuthorityClientError(RuntimeError):
    def __init__(self, code: str, hint: str) -> None:
        super().__init__(hint)
        self.code = code
        self.hint = hint


def _request(path: str, payload: dict[str, Any] | None = None, *, timeout: float, method: str = "POST") -> dict[str, Any]:
    body = b"" if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    request = (
        f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    ).encode() + body
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(EXECUTOR_SOCKET)
        connection.sendall(request)
        response = http.client.HTTPResponse(connection)
        response.begin()
        response_body = response.read()
    except (OSError, http.client.HTTPException) as exc:
        raise AuthorityClientError("SEND_EXECUTOR_UNAVAILABLE", "Trusted send executor is unavailable") from exc
    finally:
        connection.close()
    try:
        value = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise AuthorityClientError("SEND_EXECUTOR_INVALID_RESPONSE", "Trusted send executor returned invalid JSON") from exc
    if response.status < 200 or response.status >= 300 or not isinstance(value, dict):
        raise AuthorityClientError("SEND_EXECUTOR_REJECTED", "Trusted send executor rejected the exact-send request")
    return value


def _receipt_id(value: str) -> str:
    if len(value) != 71 or not value.startswith("sha256:") or any(character not in "0123456789abcdef" for character in value[7:]):
        raise AuthorityClientError("INVALID_RECEIPT_ID", "Receipt ID must be a canonical sha256 digest")
    return value


def status(receipt_id: str) -> dict[str, Any]:
    try:
        return ok("executor.status", _request(f"/v1/status/{_receipt_id(receipt_id)}", timeout=10, method="GET"))
    except AuthorityClientError as exc:
        return fail("executor.status", [error("conflict", exc.code, False, exc.hint)])


def abort(receipt_id: str) -> dict[str, Any]:
    try:
        return ok("executor.abort", _request(f"/v1/abort/{_receipt_id(receipt_id)}", {}, timeout=10))
    except AuthorityClientError as exc:
        return fail("executor.abort", [error("conflict", exc.code, False, exc.hint)])


def execute(
    thread_id: str,
    draft_id: str,
    *,
    delay: int,
    account: str | None,
    attestation_reference: str | None,
    approval_receipt: str | None,
    approval_ref: str | None = None,
    timeout: float = 220,
) -> dict[str, Any]:
    """Submit identifiers plus the receipt; trusted evidence already lives in executor state."""
    try:
        if not account:
            raise AuthorityClientError("ACCOUNT_REQUIRED", "--account is required for strict send")
        if not approval_receipt:
            prefix = "--approval-ref is audit-only and cannot authorize a send; " if approval_ref else ""
            raise AuthorityClientError("APPROVAL_RECEIPT_REQUIRED", prefix + "--approval-receipt is required")
        if delay < 0:
            raise AuthorityClientError("INVALID_DELAY", "--delay must be non-negative")
        receipt = approval.load(approval_receipt)
        approval._validate_structure(receipt)
        binding = receipt["binding"]
        attestation_id = str(binding.get("attestation_id") or "")
        if attestation_reference and attestation_reference != attestation_id:
            raise AuthorityClientError("ATTESTATION_ID_MISMATCH", "Local attestation ID differs from the trusted receipt")
        if (
            binding.get("account_email_sha256") != attestation.sha256(account.lower())
            or binding.get("thread_id_sha256") != attestation.sha256(thread_id)
            or binding.get("draft_id_sha256") != attestation.sha256(draft_id)
            or binding.get("delay_seconds") != delay
        ):
            raise AuthorityClientError("APPROVAL_BINDING_MISMATCH", "Receipt is bound to another execution")
        status = _request(
            "/v1/execute",
            {
                "receipt": receipt,
                "execution": {
                    "account": account,
                    "thread_id": thread_id,
                    "draft_id": draft_id,
                    "attestation_id": attestation_id,
                },
            },
            timeout=timeout,
        )
        state = str(status.get("state") or "unknown")
        sent = state == "provider_confirmed"
        data = {
            **status,
            "thread_id": thread_id,
            "draft_id": draft_id,
            "attestation_id": attestation_id,
            "sent": sent,
            "provider_confirmed": sent,
            "accepted": state in {"accepted", "provider_confirmed"},
            "post_claimed": state in {"claimed", "accepted", "provider_confirmed", "unknown"},
            "approval_receipt_id": receipt["receipt_id"],
            "approval_issuer": receipt["issuer"],
            "approval_key_id": receipt["key_id"],
            "approval_authority": approval.AUTHORITY,
            "approval_verified": True,
            "approval_consumed": state not in {"grace", "aborted", "failed", "expired"},
            "trusted_executor_required": True,
            "unattended_send_eligible": False,
        }
        if state in {"failed", "expired", "aborted"}:
            return fail("send", [error("conflict", str(status.get("errorCode") or state).upper(), False, f"Trusted executor ended in {state}")])
        return ok("send", data)
    except AuthorityClientError as exc:
        return fail("send", [error("conflict", exc.code, False, exc.hint)])
    except approval.ApprovalError as exc:
        return fail("send", [error("conflict", exc.code, False, exc.hint)])
