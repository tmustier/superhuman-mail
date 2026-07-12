"""Credential-bridge-only provider contract for the trusted send executor.

The public ``shm`` binary exposes these operations so a separately signed native
bridge can invoke one fixed provider API. They are not an approval authority:
the isolated executor must first verify and durably claim a signed receipt. The
bridge is the only production process that may supply Superhuman credentials.
"""
from __future__ import annotations

import base64
import hmac
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from . import approval, attestation, lifecycle, send

CONTRACT_VERSION = "shm-executor/v1"
CONTRACT = {
    "schema": CONTRACT_VERSION,
    "prepare": {
        "command": "draft prepare",
        "result": "trusted-attestation-bundle",
        "requires": ["account", "thread_id", "draft_id", "delay"],
    },
    "render": {
        "command": "draft get",
        "result": "content-free-render-envelope",
        "requires": ["account", "thread_id", "draft_id", "imported_attestation_id"],
    },
    "send": {
        "command": "draft send",
        "preconditions": ["if_revision", "expected_draft_fingerprint"],
        "result": "provider-confirmed-or-truthful-unknown",
        "definitive_pre_post_exit": 10,
    },
    "approval_binding_schema": approval.SCHEMA,
    "raw_send_fallback": False,
}


def require_credential_bridge() -> None:
    """Reject ordinary desktop-cookie execution of the bridge-only verbs."""
    if os.environ.get("SHM_AUTH_TOKEN_STDIN") != "1":
        raise ExecutorContractError(
            "CREDENTIAL_BRIDGE_REQUIRED",
            "This operation is available only through the signed credential bridge",
        )


class ExecutorContractError(RuntimeError):
    """A deterministic provider-side precondition failed before POST."""

    def __init__(self, code: str, hint: str) -> None:
        super().__init__(hint)
        self.code = code
        self.hint = hint


def _load_bound(
    reference: str | Path,
    *,
    account: str,
    thread_id: str,
    draft_id: str,
) -> dict[str, Any]:
    record = attestation.load(reference)
    attestation.verify_for_executor(record)
    if str(record.get("thread_id")) != thread_id or str(record.get("draft_id")) != draft_id:
        raise ExecutorContractError("DRAFT_BINDING_MISMATCH", "Attestation belongs to another draft")
    if str((record.get("account") or {}).get("email", "")).lower() != account.lower():
        raise ExecutorContractError("ACCOUNT_BINDING_MISMATCH", "Attestation belongs to another account")
    return record


def prepare_attestation(
    thread_id: str,
    draft_id: str,
    *,
    account: str,
    delay: int,
    renderer: attestation.Renderer | None = None,
) -> dict[str, Any]:
    """Create evidence inside the credential authority, never from worker bytes."""
    if delay < 0:
        raise ExecutorContractError("INVALID_DELAY", "Delay must be non-negative")
    state = Path(os.environ.get("SHM_STATE_DIR") or "")
    if not state.is_absolute():
        raise ExecutorContractError("EXECUTOR_STATE_REQUIRED", "Executor state directory must be absolute")
    prepare_root = state / "prepared-renders"
    prepare_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = Path(tempfile.mkdtemp(prefix="render-", dir=prepare_root))
    record = attestation.create(
        thread_id,
        draft_id,
        account=account,
        output_dir=output,
        delay=delay,
        renderer=renderer,
    )
    identity = attestation.canonical_bytes(attestation.identity_content(record))
    screenshots = []
    for item in record["screenshots"]:
        data = Path(item["path"]).read_bytes()
        screenshots.append({
            "role": item["role"],
            "sha256": item["sha256"],
            "media_type": "image/png",
            "data_base64": base64.b64encode(data).decode(),
        })
    return {
        "record": {key: value for key, value in record.items() if key != "artifact_path"},
        "identity_base64": base64.b64encode(identity).decode(),
        "screenshots": screenshots,
    }


def revision_id(record: dict[str, Any]) -> str:
    """Opaque immutable revision bound to server history and attestation identity."""
    return attestation.sha256(attestation.canonical_bytes({
        "history_id": record.get("history_id"),
        "attestation_id": record.get("attestation_id"),
    }))


def get_rendered(
    thread_id: str,
    draft_id: str,
    *,
    account: str,
    attestation_reference: str | Path,
    renderer: attestation.Renderer | None = None,
) -> dict[str, Any]:
    """Rerender and return only the flat, content-free approval binding."""
    record = _load_bound(
        attestation_reference,
        account=account,
        thread_id=thread_id,
        draft_id=draft_id,
    )
    verified = attestation.revalidate_for_send(record, account=account, renderer=renderer)
    exact = str((verified.get("fingerprint") or {}).get("exact") or "")
    if not hmac.compare_digest(exact, str((record.get("fingerprint") or {}).get("exact") or "")):
        raise ExecutorContractError("DRAFT_FINGERPRINT_MISMATCH", "Live draft differs from approval")
    return {
        "schema": CONTRACT_VERSION,
        "revision_id": revision_id(record),
        "draft_fingerprint": exact,
        "approval_binding": approval.binding_for_attestation(record),
        "attestation_id": record["attestation_id"],
    }


def _observe_result(record: dict[str, Any], *, account: str, wait: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, wait)
    interval = 0.25
    state: dict[str, Any] | None = None
    while True:
        state, _wrapper, _warnings = lifecycle.observe(
            str(record["thread_id"]),
            str(record["draft_id"]),
            account=account,
            require_explicit_account=True,
            attempt_superhuman_id=str(record["superhuman_id"]),
        )
        if state["state"] in {
            lifecycle.PROVIDER_CONFIRMED,
            lifecycle.BACKEND_CONFIRMED,
            lifecycle.SCHEDULED,
            lifecycle.FAILED,
            lifecycle.ABORTED,
            lifecycle.INCONSISTENT,
        }:
            break
        now = time.monotonic()
        if now >= deadline:
            break
        time.sleep(min(interval, max(0.0, deadline - now)))
        interval = min(interval * 2, 5.0)
    assert state is not None
    provider_confirmed = state["state"] == lifecycle.PROVIDER_CONFIRMED
    return {
        "schema": CONTRACT_VERSION,
        "state": state["state"],
        "accepted": state["state"] in {
            lifecycle.SCHEDULED,
            lifecycle.BACKEND_CONFIRMED,
            lifecycle.PROVIDER_CONFIRMED,
        },
        "provider_confirmed": provider_confirmed,
        "provider_message_id": (state.get("provider_message") or {}).get("id"),
        "outbound_evidence": bool(state.get("outbound_evidence")),
    }


def send_conditional(
    thread_id: str,
    draft_id: str,
    *,
    account: str,
    attestation_reference: str | Path,
    if_revision: str,
    expected_draft_fingerprint: str,
    delay: int,
    wait: float,
    renderer: attestation.Renderer | None = None,
) -> dict[str, Any]:
    """Perform one conditional POST after a second exact renderer probe.

    The trusted executor owns durable single-use claiming. This function makes
    no retry after calling the transport; an exception at that boundary is an
    ambiguous provider outcome to the caller.
    """
    record = _load_bound(
        attestation_reference,
        account=account,
        thread_id=thread_id,
        draft_id=draft_id,
    )
    if delay < 0 or int(record.get("delay_seconds", -1)) != delay:
        raise ExecutorContractError("DELAY_MISMATCH", "Delay differs from the approved attestation")
    expected_revision = revision_id(record)
    if not hmac.compare_digest(if_revision, expected_revision):
        raise ExecutorContractError("REVISION_MISMATCH", "Draft revision precondition failed")
    approved_fingerprint = str((record.get("fingerprint") or {}).get("exact") or "")
    if not hmac.compare_digest(expected_draft_fingerprint, approved_fingerprint):
        raise ExecutorContractError("DRAFT_FINGERPRINT_MISMATCH", "Draft fingerprint precondition failed")

    verified = attestation.revalidate_for_send(record, account=account, renderer=renderer)
    live_fingerprint = str((verified.get("fingerprint") or {}).get("exact") or "")
    if not hmac.compare_digest(live_fingerprint, expected_draft_fingerprint):
        raise ExecutorContractError("DRAFT_FINGERPRINT_MISMATCH", "Live draft changed before POST")
    send._post_exact_payload(verified["outgoing_payload"], delay=delay)
    return _observe_result(record, account=account, wait=wait)
