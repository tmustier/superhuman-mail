"""Thin credential-free client for the one canonical send authority."""
from __future__ import annotations

import base64
import http.client
import json
import socket
from pathlib import Path
from typing import Any

from . import approval, attestation
from ._envelope import error, fail, ok

EXECUTOR_SOCKET = "/var/run/superhuman-mail-send-executor.sock"


class AuthorityClientError(RuntimeError):
    def __init__(self, code: str, hint: str) -> None:
        super().__init__(hint)
        self.code = code
        self.hint = hint


def _request(path: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    request = (
        f"POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
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


def _bundle(record: dict[str, Any]) -> dict[str, Any]:
    screenshots = []
    for item in record.get("screenshots") or []:
        path = Path(str(item.get("path") or ""))
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise AuthorityClientError("ATTESTATION_ARTIFACT_MISMATCH", "An attested screenshot is unavailable") from exc
        digest = attestation.sha256(data)
        if digest != item.get("sha256"):
            raise AuthorityClientError("ATTESTATION_ARTIFACT_MISMATCH", "An attested screenshot differs from its signed hash")
        screenshots.append({
            "sha256": digest,
            "media_type": "image/png",
            "data_base64": base64.b64encode(data).decode(),
        })
    portable = {key: value for key, value in record.items() if key != "artifact_path"}
    return {"record": portable, "screenshots": screenshots}


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
    """Import immutable evidence, then ask the isolated executor to consume it."""
    try:
        if not account:
            raise AuthorityClientError("ACCOUNT_REQUIRED", "--account is required for strict send")
        if not attestation_reference:
            raise AuthorityClientError("ATTESTATION_REQUIRED", "--attestation is required for strict send")
        if not approval_receipt:
            prefix = "--approval-ref is audit-only and cannot authorize a send; " if approval_ref else ""
            raise AuthorityClientError("APPROVAL_RECEIPT_REQUIRED", prefix + "--approval-receipt is required")
        if delay < 0:
            raise AuthorityClientError("INVALID_DELAY", "--delay must be non-negative")
        record = attestation.load(attestation_reference)
        attestation.verify(record)
        if record.get("thread_id") != thread_id or record.get("draft_id") != draft_id:
            raise AuthorityClientError("ATTESTATION_DRAFT_MISMATCH", "Attestation belongs to a different draft/thread")
        if str((record.get("account") or {}).get("email", "")).lower() != account.lower():
            raise AuthorityClientError("ACCOUNT_BINDING_MISMATCH", "Attestation belongs to a different account")
        if int(record.get("delay_seconds", -1)) != delay:
            raise AuthorityClientError("ATTESTATION_DELAY_MISMATCH", "Send delay differs from the approved attestation")
        receipt = approval.load(approval_receipt)
        approval.verify(receipt, attestation=record)
        bundle = _bundle(record)
        _request(
            "/v1/import-attestation",
            {"receipt": receipt, "attestation_bundle": bundle},
            timeout=min(timeout, 60),
        )
        status = _request(
            "/v1/execute",
            {
                "receipt": receipt,
                "execution": {
                    "account": account,
                    "thread_id": thread_id,
                    "draft_id": draft_id,
                    "attestation_id": record["attestation_id"],
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
            "attestation_id": record["attestation_id"],
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
    except attestation.AttestationError as exc:
        return fail("send", [error("conflict", exc.code, False, exc.hint)])
    except approval.ApprovalError as exc:
        return fail("send", [error("conflict", exc.code, False, exc.hint)])
