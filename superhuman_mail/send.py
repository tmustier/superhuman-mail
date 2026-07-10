"""Send operations — validate and execute.

Send is IRREVERSIBLE. The CLI requires --dry-run or --confirm.
"""
from __future__ import annotations

import re
import time
import urllib.request
import uuid
from email.headerregistry import Address
from email.utils import parseaddr
from html import unescape
from typing import Any

from . import _auth, attestation as _attestation, attempts as _attempts, lifecycle
from ._envelope import classify_exception, error, fail, ok

# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def _base36(value: int) -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    out = ""
    while value:
        value, rem = divmod(value, 36)
        out = chars[rem] + out
    return out


def _superhuman_id() -> str:
    now_ms = int(time.time() * 1000)
    bounded = min(max(now_ms, 36**7), 36**8 - 1)
    return f"{_base36(bounded)}.{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# Contact formatting
# ---------------------------------------------------------------------------


def _contact_json(contact: Any) -> dict[str, str]:
    if isinstance(contact, dict):
        email = str(contact.get("email", "")).strip()
        name = str(contact.get("name", "")).strip()
        result: dict[str, str] = {"email": email}
        if name:
            result["name"] = name
        if contact.get("id"):
            result["id"] = str(contact["id"])
        return result
    raw = str(contact or "").strip()
    name, email = parseaddr(raw)
    result = {"email": email or raw}
    if name:
        result["name"] = name
    return result


def _attachments_json(attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for a in attachments or []:
        src = a.get("source") or {}
        out.append({
            "uuid": a.get("uuid"),
            "cid": a.get("cid"),
            "name": a.get("name"),
            "type": a.get("type"),
            "inline": bool(a.get("inline")),
            "source": {
                "type": src.get("type"),
                "thread_id": src.get("threadId"),
                "message_id": src.get("messageId"),
                "attachment_id": src.get("attachmentId"),
                "fixed_part_id": src.get("fixedPartId"),
                "uuid": src.get("uuid"),
                "cid": src.get("cid"),
            },
        })
    return out


def _merge_message_attachments(draft: dict[str, Any], msg_data: dict[str, Any]) -> dict[str, Any]:
    """Merge message-level attachments into the draft.

    Superhuman stores draft attachments at the message level
    (``messages/{draft_id}/attachments/{uuid}``), not on the draft object
    itself (``_to_backend`` strips ``attachments`` on write). The app merges
    them at send time; do the same here so sends include attachments.
    """
    existing = {str(a.get("uuid")) for a in (draft.get("attachments") or [])}
    merged = list(draft.get("attachments") or [])
    msg_atts = msg_data.get("attachments") or {}
    for att_uuid, att in sorted(msg_atts.items()):
        if not isinstance(att, dict) or att.get("discardedAt"):
            continue
        if str(att.get("uuid") or att_uuid) in existing:
            continue
        merged.append(att)
    if merged:
        draft = {**draft, "attachments": merged}
    return draft


# ---------------------------------------------------------------------------
# Build outgoing message
# ---------------------------------------------------------------------------


def _build_outgoing(draft: dict[str, Any], sid: str | None = None) -> dict[str, Any]:
    sid = sid or _superhuman_id()
    thread_id = str(draft["threadId"])
    message_id = str(draft["id"])

    headers = [
        {"name": "X-Mailer", "value": "Superhuman Web (superhuman-mail)"},
        {"name": "X-Superhuman-ID", "value": sid},
        {"name": "X-Superhuman-Draft-ID", "value": message_id},
    ]
    if thread_id.startswith("draft"):
        headers.append({"name": "X-Superhuman-Thread-ID", "value": thread_id})
    if draft.get("inReplyToRfc822Id"):
        headers.append({"name": "In-Reply-To", "value": draft["inReplyToRfc822Id"]})
    refs = [r for r in (draft.get("references") or []) if r]
    if refs:
        headers.append({"name": "References", "value": " ".join(refs)})

    payload: dict[str, Any] = {
        "headers": headers,
        "superhuman_id": sid,
        "rfc822_id": draft.get("rfc822Id"),
        "thread_id": thread_id,
        "message_id": message_id,
        "in_reply_to": draft.get("inReplyTo"),
        "from": _contact_json(draft.get("from")),
        "to": [_contact_json(c) for c in (draft.get("to") or [])],
        "cc": [_contact_json(c) for c in (draft.get("cc") or [])],
        "bcc": [_contact_json(c) for c in (draft.get("bcc") or [])],
        "subject": draft.get("subject", ""),
        "html_body": draft.get("htmlBody") or draft.get("body") or "",
        "attachments": _attachments_json(draft.get("attachments")),
    }

    for key, val in {
        "scheduled_for": draft.get("scheduledFor"),
        "abort_on_reply": draft.get("abortOnReply"),
        # Current Superhuman OutgoingMessage.fromDraft() does not copy the
        # draft reminder into toJsonRequest(); the persisted draft owns it.
        "sensitivity_label_id": draft.get("sensitivityLabelId"),
        "sensitivity_tenant_id": draft.get("sensitivityTenantId"),
    }.items():
        if val not in (None, [], "", False):
            payload[key] = val

    return payload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SEND_DELAY_SECONDS = 20
APPROVAL_AUTHORITY = "correlation_only"


class SendSafetyError(RuntimeError):
    """A deterministic execute-time safety guard rejected a draft."""

    def __init__(self, code: str, hint: str, *, cls: str = "conflict") -> None:
        super().__init__(hint)
        self.code = code
        self.hint = hint
        self.cls = cls


def _safety_failure(command: str, exc: SendSafetyError) -> dict[str, Any]:
    return fail(command, [error(exc.cls, exc.code, False, exc.hint)])


def _valid_address(contact: Any) -> bool:
    normalized = _contact_json(contact)
    value = normalized.get("email", "").strip()
    if not value or any(ch.isspace() for ch in value):
        return False
    try:
        parsed = Address(addr_spec=value)
    except (TypeError, ValueError):
        return False
    return bool(parsed.username and parsed.domain and "." in parsed.domain.rstrip("."))


def _body_has_content(value: str) -> bool:
    if not value or not value.strip():
        return False
    text = unescape(re.sub(r"<[^>]*>", " ", value)).replace("\u200b", "").replace("\ufeff", "")
    if text.strip():
        return True
    return bool(re.search(r"<(?:img|ul|ol|li|table|tr|td|blockquote)\b", value, flags=re.IGNORECASE))


def _preflight(
    thread_id: str,
    draft_id: str,
    *,
    account: str | None = None,
    sid: str | None = None,
    require_explicit_account: bool = False,
    allow_empty_subject: bool = False,
    attempt_superhuman_id: str | None = None,
) -> dict[str, Any]:
    """Authoritative read-only guard used by both validate and execute."""
    try:
        state, wrapper, warnings = lifecycle.observe(
            thread_id,
            draft_id,
            account=account,
            require_explicit_account=require_explicit_account,
            attempt_superhuman_id=attempt_superhuman_id,
        )
    except LookupError as exc:
        raise SendSafetyError("DRAFT_NOT_FOUND", str(exc), cls="not-found") from exc
    except lifecycle.AccountBindingError as exc:
        raise SendSafetyError("ACCOUNT_BINDING_MISMATCH", str(exc), cls="input") from exc

    state_name = state["state"]
    if state_name == lifecycle.PROVIDER_CONFIRMED:
        raise SendSafetyError("DRAFT_ALREADY_SENT", "Draft already has an immutable provider-confirmed sent message")
    if state_name == lifecycle.BACKEND_CONFIRMED:
        raise SendSafetyError("DRAFT_ALREADY_SENT", "Draft has a terminal backend send job and cannot be submitted again")
    if state_name == lifecycle.DISCARDED:
        raise SendSafetyError("DRAFT_DISCARDED", "Draft has been discarded")
    if state_name in {lifecycle.SCHEDULED, lifecycle.REQUESTED, lifecycle.PENDING_UNDO}:
        raise SendSafetyError("SEND_ALREADY_PENDING", f"Draft already has a nonterminal send job ({state_name})")
    if state_name in {lifecycle.FAILED, lifecycle.ABORTED}:
        raise SendSafetyError("TERMINAL_SEND_JOB", f"Draft has a terminal send job ({state_name}); create a new draft")
    if state_name == lifecycle.INCONSISTENT:
        raise SendSafetyError("LIFECYCLE_INCONSISTENT", "Draft has contradictory send evidence; reconcile it before any send")
    if state_name != lifecycle.ACTIVE:
        raise SendSafetyError("DRAFT_NOT_ACTIVE", f"Draft is not active ({state_name})")

    draft = wrapper.get("draft")
    if not isinstance(draft, dict):
        raise SendSafetyError("DRAFT_NOT_FOUND", f"Draft {draft_id} not found", cls="not-found")
    draft = _merge_message_attachments(draft, wrapper)

    recipients = list(draft.get("to") or []) + list(draft.get("cc") or []) + list(draft.get("bcc") or [])
    if not recipients:
        raise SendSafetyError("RECIPIENTS_REQUIRED", "Draft has no recipients", cls="input")
    invalid = [_contact_json(item).get("email", "") for item in recipients if not _valid_address(item)]
    if invalid:
        raise SendSafetyError("INVALID_RECIPIENT", "Draft contains an invalid recipient address", cls="input")

    from_email = _contact_json(draft.get("from")).get("email", "").lower()
    bound_email = str(state["account"]["email"]).lower()
    if not from_email or from_email != bound_email:
        raise SendSafetyError(
            "FROM_ACCOUNT_MISMATCH",
            f"Draft From address is not the bound account {state['account']['email']}",
            cls="input",
        )

    if not _body_has_content(str(draft.get("htmlBody") or draft.get("body") or "")):
        raise SendSafetyError("BODY_REQUIRED", "Draft body is empty", cls="input")
    if not allow_empty_subject and not str(draft.get("subject") or "").strip():
        raise SendSafetyError("SUBJECT_REQUIRED", "Draft subject is empty and has not been explicitly attested", cls="input")

    outgoing = _build_outgoing(draft, sid=sid)
    return {
        "thread_id": thread_id,
        "draft_id": draft_id,
        "draft": draft,
        "wrapper": wrapper,
        "lifecycle": state,
        "outgoing": outgoing,
        "warnings": warnings,
    }


def validate(thread_id: str, draft_id: str, *, account: str | None = None) -> dict[str, Any]:
    """Read-only metadata preflight. Exact render attestation remains required."""
    try:
        if not account:
            raise SendSafetyError("ACCOUNT_REQUIRED", "--account is required for send preflight", cls="input")
        checked = _preflight(
            thread_id,
            draft_id,
            account=account,
            require_explicit_account=True,
        )
        draft = checked["draft"]
        outgoing = checked["outgoing"]
        result: dict[str, Any] = {
            "thread_id": thread_id,
            "draft_id": draft_id,
            "sendable": True,
            "send_eligible": False,
            "render_attested": False,
            "action": draft.get("action", "compose"),
            "from": outgoing.get("from"),
            "to": outgoing.get("to"),
            "cc": outgoing.get("cc"),
            "bcc_count": len(outgoing.get("bcc") or []),
            "subject": outgoing.get("subject"),
            "body_preview": (draft.get("snippet") or "")[:200],
            "has_attachments": bool(outgoing.get("attachments")),
            "lifecycle": checked["lifecycle"],
            "approval_authority": APPROVAL_AUTHORITY,
            "approval_verified": False,
            "unattended_send_eligible": False,
            "next_step": "Create an exact render attestation and obtain external operator approval before --confirm",
        }
        for draft_key, result_key in {
            "scheduledFor": "scheduled_for",
            "abortOnReply": "abort_on_reply",
            "reminder": "reminder",
            "sensitivityLabelId": "sensitivity_label_id",
            "sensitivityTenantId": "sensitivity_tenant_id",
        }.items():
            val = draft.get(draft_key)
            if val not in (None, [], "", False):
                result[result_key] = val
        return ok("send.validate", result, warnings=checked["warnings"])
    except SendSafetyError as exc:
        return _safety_failure("send.validate", exc)
    except Exception as exc:
        return fail("send.validate", [classify_exception(exc)])


def _post_exact_payload(outgoing: dict[str, Any], *, delay: int) -> None:
    """POST freshly probed exact payload bytes; caller owns idempotent claim."""
    request_body = {
        "version": 3,
        "outgoing_message": outgoing,
        "delay": delay,
        "is_multi_recipient": True,
    }
    req = urllib.request.Request(
        "https://mail.superhuman.com/~backend/messages/send",
        data=_attestation.canonical_bytes(request_body),
        headers={**_auth.api_headers(), "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def _request_accepted(attempt: dict[str, Any], state: dict[str, Any]) -> bool:
    return bool(
        attempt.get("response_class") == "http_2xx"
        or state["state"] in {
            lifecycle.SCHEDULED,
            lifecycle.BACKEND_CONFIRMED,
            lifecycle.PROVIDER_CONFIRMED,
            lifecycle.FAILED,
            lifecycle.ABORTED,
        }
    )


def _attempt_result(attempt: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    provider_confirmed = state["state"] == lifecycle.PROVIDER_CONFIRMED
    post_claimed = int(attempt["post_count"]) > 0
    accepted = _request_accepted(attempt, state)
    material_state = state["state"]
    if post_claimed and material_state == lifecycle.ACTIVE and attempt.get("state") == "unknown":
        material_state = "unknown"
    return {
        "attempt_id": attempt["attempt_id"],
        "thread_id": attempt["thread_id"],
        "draft_id": attempt["draft_id"],
        "attestation_id": attempt["attestation_id"],
        "state": material_state,
        "post_claimed": post_claimed,
        "accepted": accepted,
        "sent": provider_confirmed,
        "provider_confirmed": provider_confirmed,
        "outbound_evidence": bool(state["outbound_evidence"]),
        "superhuman_id": attempt["superhuman_id"],
        "provider_message_id": (state.get("provider_message") or {}).get("id"),
        "approval_authority": APPROVAL_AUTHORITY,
        "approval_verified": False,
        "unattended_send_eligible": False,
        "idempotency_scope": _attempts.IDEMPOTENCY_SCOPE,
        "lifecycle": state,
    }


def _reconcile(
    attempt: dict[str, Any],
    *,
    account: str,
    wait: float,
    journal: _attempts.AttemptJournal,
    sleep: Any = time.sleep,
    monotonic: Any = time.monotonic,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Poll backend/provider evidence without ever creating a new identity."""
    deadline = monotonic() + max(0.0, wait)
    interval = 0.25
    warnings: list[str] = []
    last_state: dict[str, Any] | None = None
    last_attempt = attempt

    while True:
        state, _wrapper, observed_warnings = lifecycle.observe(
            attempt["thread_id"],
            attempt["draft_id"],
            account=account,
            require_explicit_account=True,
            attempt_superhuman_id=attempt["superhuman_id"],
        )
        warnings.extend(item for item in observed_warnings if item not in warnings)
        if (
            attempt.get("state") == lifecycle.PROVIDER_CONFIRMED
            and attempt.get("provider_message_id")
            and state["state"] != lifecycle.PROVIDER_CONFIRMED
        ):
            # An immutable provider confirmation already recorded by this
            # attempt cannot be downgraded by a later stale/empty local cache.
            preserved = {
                **state,
                "state": lifecycle.PROVIDER_CONFIRMED,
                "terminal": True,
                "send_blocked": True,
                "outbound_evidence": True,
                "confidence": "provider_confirmed_journal",
                "consistency": "observation_lag",
                "provider_message": {
                    **(state.get("provider_message") or {}),
                    "id": attempt["provider_message_id"],
                },
            }
            warnings.append("Current provider cache lags a previously recorded immutable confirmation")
            return attempt, preserved, warnings
        last_state = state
        update: dict[str, Any] = {"last_reconciled_at": datetime_now_iso()}
        state_name = state["state"]
        if state_name == lifecycle.PROVIDER_CONFIRMED:
            provider = state.get("provider_message") or {}
            update.update({
                "provider_message_id": provider.get("id"),
                "provider_sent_at": state.get("timestamps", {}).get("provider_message_at"),
            })
            last_attempt = journal.update(attempt["attempt_id"], state=state_name, **update)
            return last_attempt, state, warnings
        if state_name == lifecycle.BACKEND_CONFIRMED:
            update["backend_sent_at"] = state.get("timestamps", {}).get("sent_at")
            last_attempt = journal.update(attempt["attempt_id"], state=state_name, **update)
        elif state_name in {lifecycle.FAILED, lifecycle.ABORTED, lifecycle.SCHEDULED}:
            last_attempt = journal.update(attempt["attempt_id"], state=state_name, **update)
            return last_attempt, state, warnings
        elif state_name == lifecycle.INCONSISTENT:
            last_attempt = journal.update(attempt["attempt_id"], state="unknown", **update)
            return last_attempt, state, warnings
        else:
            if (
                state_name == lifecycle.ACTIVE
                and int(attempt["post_count"]) == 0
                and attempt.get("state") == "prepared"
            ):
                # A read-tier status check must not convert a never-posted,
                # safely claimable row into an irreversible unknown outcome.
                last_attempt = journal.update(attempt["attempt_id"], **update)
                return last_attempt, state, warnings
            else:
                # An active source after a POST claim is an unknown/lagging
                # outcome, never permission to mint another ID or post again.
                attempt_state = state_name if state_name != lifecycle.ACTIVE else "unknown"
                last_attempt = journal.update(attempt["attempt_id"], state=attempt_state, **update)

        now = monotonic()
        if now >= deadline:
            assert last_state is not None
            return last_attempt, last_state, warnings
        sleep(min(interval, max(0.0, deadline - now)))
        interval = min(interval * 2, 5.0)


def datetime_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def status(
    thread_id: str,
    draft_id: str,
    *,
    account: str,
    wait: float = 0,
    journal: _attempts.AttemptJournal | None = None,
) -> dict[str, Any]:
    """Reconcile one draft/attempt and return a truthful typed state."""
    try:
        identity, warnings = lifecycle.resolve_account(account, require_explicit=True)
        journal = journal or _attempts.AttemptJournal()
        attempt = journal.get(identity["provider_user_id"], draft_id)
        if attempt:
            if attempt["thread_id"] != thread_id:
                raise SendSafetyError(
                    "ATTEMPT_THREAD_MISMATCH",
                    "The local attempt for this draft belongs to a different thread",
                )
            updated, state, observed_warnings = _reconcile(
                attempt,
                account=identity["email"],
                wait=wait,
                journal=journal,
            )
            return ok("send.status", _attempt_result(updated, state), warnings=warnings + observed_warnings)
        deadline = time.monotonic() + max(0.0, wait)
        interval = 0.25
        observed_warnings: list[str] = []
        while True:
            state, _wrapper, current_warnings = lifecycle.observe(
                thread_id,
                draft_id,
                account=identity["email"],
                require_explicit_account=True,
            )
            observed_warnings.extend(item for item in current_warnings if item not in observed_warnings)
            state_name = state["state"]
            if state_name in {
                lifecycle.ACTIVE,
                lifecycle.SCHEDULED,
                lifecycle.PROVIDER_CONFIRMED,
                lifecycle.FAILED,
                lifecycle.ABORTED,
                lifecycle.DISCARDED,
                lifecycle.INCONSISTENT,
            }:
                break
            now = time.monotonic()
            if now >= deadline:
                break
            time.sleep(min(interval, max(0.0, deadline - now)))
            interval = min(interval * 2, 5.0)
        return ok("send.status", {
            "attempt_id": None,
            "attestation_id": None,
            "thread_id": thread_id,
            "draft_id": draft_id,
            "state": state["state"],
            "post_claimed": False,
            "accepted": state["state"] in {
                lifecycle.SCHEDULED,
                lifecycle.REQUESTED,
                lifecycle.PENDING_UNDO,
                lifecycle.BACKEND_CONFIRMED,
                lifecycle.PROVIDER_CONFIRMED,
                lifecycle.FAILED,
                lifecycle.ABORTED,
            },
            "sent": state["state"] == lifecycle.PROVIDER_CONFIRMED,
            "provider_confirmed": state["state"] == lifecycle.PROVIDER_CONFIRMED,
            "outbound_evidence": state["outbound_evidence"],
            "superhuman_id": (state.get("send_job") or {}).get("superhuman_id"),
            "provider_message_id": (state.get("provider_message") or {}).get("id"),
            "approval_authority": APPROVAL_AUTHORITY,
            "approval_verified": False,
            "unattended_send_eligible": False,
            "idempotency_scope": _attempts.IDEMPOTENCY_SCOPE,
            "lifecycle": state,
        }, warnings=warnings + observed_warnings)
    except SendSafetyError as exc:
        return _safety_failure("send.status", exc)
    except Exception as exc:
        return fail("send.status", [classify_exception(exc)])


def execute(
    thread_id: str,
    draft_id: str,
    *,
    delay: int = SEND_DELAY_SECONDS,
    account: str | None = None,
    attestation: str | None = None,
    approval_ref: str | None = None,
    wait: float = 120,
    journal: _attempts.AttemptJournal | None = None,
    renderer: _attestation.Renderer | None = None,
) -> dict[str, Any]:
    """Strict, locally idempotent exact-render send execution.

    The external grace period must finish before this function is invoked.  It
    performs the mandatory second no-write renderer probe immediately before
    the one locally claimed POST, then waits for provider confirmation.
    """
    try:
        if not account:
            raise SendSafetyError("ACCOUNT_REQUIRED", "--account is required for strict send", cls="input")
        if not attestation:
            # Even a malformed invocation runs the lifecycle/envelope/body
            # guard inside execute, closing the terminal-draft resend path.
            _preflight(
                thread_id,
                draft_id,
                account=account,
                require_explicit_account=True,
                allow_empty_subject=True,
            )
            raise SendSafetyError("ATTESTATION_REQUIRED", "--attestation is required for strict send", cls="input")
        if not approval_ref or not approval_ref.strip():
            raise SendSafetyError("APPROVAL_REFERENCE_REQUIRED", "--approval-ref is required for strict send", cls="input")
        if len(approval_ref) > 512:
            raise SendSafetyError("APPROVAL_REFERENCE_INVALID", "--approval-ref must be 512 characters or fewer", cls="input")
        if delay < 0:
            raise SendSafetyError("INVALID_DELAY", "--delay must be non-negative", cls="input")

        record = _attestation.load(attestation)
        _attestation.verify(record)
        if str(record.get("thread_id")) != thread_id or str(record.get("draft_id")) != draft_id:
            raise SendSafetyError("ATTESTATION_DRAFT_MISMATCH", "Attestation belongs to a different draft/thread")
        if int(record.get("delay_seconds", -1)) != delay:
            raise SendSafetyError("ATTESTATION_DELAY_MISMATCH", "Send delay differs from the approved attestation")
        identity, account_warnings = lifecycle.resolve_account(account, require_explicit=True)
        if str(record.get("account", {}).get("provider_user_id")) != identity["provider_user_id"]:
            raise SendSafetyError("ACCOUNT_BINDING_MISMATCH", "Attestation belongs to a different immutable account identity")

        journal = journal or _attempts.AttemptJournal()
        attempt = journal.get(identity["provider_user_id"], draft_id)
        if attempt is not None:
            # Validate that this invocation carries the same approved identity.
            attempt, _created = journal.create_or_get(
                account_id=identity["provider_user_id"],
                account_hash=_attestation.sha256(identity["email"].lower()),
                thread_id=thread_id,
                draft_id=draft_id,
                attestation_id=str(record["attestation_id"]),
                approval_ref=approval_ref,
                superhuman_id=str(record["superhuman_id"]),
                outgoing_fingerprint=str(record["fingerprint"]["exact"]),
            )

        if attempt is not None and (int(attempt["post_count"]) > 0 or attempt["state"] != "prepared"):
            updated, state, warnings = _reconcile(
                attempt,
                account=identity["email"],
                wait=wait,
                journal=journal,
            )
            data = _attempt_result(updated, state)
            if state["state"] in {lifecycle.FAILED, lifecycle.ABORTED, lifecycle.INCONSISTENT}:
                code = "SEND_TERMINAL_FAILURE" if state["state"] != lifecycle.INCONSISTENT else "LIFECYCLE_INCONSISTENT"
                return fail("send", [error("conflict", code, False, f"Attempt reconciled to {state['state']}")], warnings=warnings)
            if not data["sent"] and state["state"] != lifecycle.SCHEDULED:
                if data["post_claimed"]:
                    warnings.append("Attempt remains pending/unknown; no additional POST was made")
                else:
                    warnings.append("No local POST was claimed; lifecycle evidence is not provider-confirmed")
            return ok("send", data, warnings=account_warnings + warnings)

        if attempt is None:
            # New attempts must pass the authoritative execute-time guard.
            # Existing attempts reconcile even when the source is now pending
            # or terminal; retry must not be mistaken for a second send.
            _preflight(
                thread_id,
                draft_id,
                account=identity["email"],
                require_explicit_account=True,
                allow_empty_subject=True,
            )

        # This is the mandatory post-grace, immediately pre-POST exact probe.
        # Persist the attempt only after this probe succeeds, so a stale or
        # unavailable renderer never strands the draft behind a no-POST row.
        verified = _attestation.revalidate_for_send(
            record,
            account=identity["email"],
            renderer=renderer,
        )
        if attempt is None:
            attempt, _created = journal.create_or_get(
                account_id=identity["provider_user_id"],
                account_hash=_attestation.sha256(identity["email"].lower()),
                thread_id=thread_id,
                draft_id=draft_id,
                attestation_id=str(record["attestation_id"]),
                approval_ref=approval_ref,
                superhuman_id=str(record["superhuman_id"]),
                outgoing_fingerprint=str(record["fingerprint"]["exact"]),
            )
        try:
            attempt, claimed = journal.claim_post(attempt["attempt_id"])
        except KeyError as exc:
            replacement = journal.get(identity["provider_user_id"], draft_id)
            if replacement is not None:
                raise _attempts.AttemptConflict(
                    "Attempt identity changed concurrently; inspect send status before retrying"
                ) from exc
            raise
        if not claimed:
            updated, state, warnings = _reconcile(
                attempt,
                account=identity["email"],
                wait=wait,
                journal=journal,
            )
            return ok("send", _attempt_result(updated, state), warnings=account_warnings + warnings)

        try:
            _post_exact_payload(verified["outgoing_payload"], delay=delay)
            attempt = journal.update(attempt["attempt_id"], state="request_accepted", response_class="http_2xx")
        except Exception as transport_exc:
            classified = classify_exception(transport_exc)
            attempt = journal.update(
                attempt["attempt_id"],
                state="unknown",
                response_class=classified["code"],
                last_error=f"{type(transport_exc).__name__}",
            )
            # A lost response may have followed a successful server accept. Reconcile;
            # never automatically POST again after the pre-network claim.

        updated, state, warnings = _reconcile(
            attempt,
            account=identity["email"],
            wait=wait,
            journal=journal,
        )
        data = _attempt_result(updated, state)
        if state["state"] in {lifecycle.FAILED, lifecycle.ABORTED, lifecycle.INCONSISTENT}:
            code = "SEND_TERMINAL_FAILURE" if state["state"] != lifecycle.INCONSISTENT else "LIFECYCLE_INCONSISTENT"
            return fail("send", [error("conflict", code, False, f"Attempt reconciled to {state['state']}")], warnings=warnings)
        if not data["sent"] and state["state"] != lifecycle.SCHEDULED:
            if data["accepted"]:
                warnings.append("Request was accepted; provider delivery is not yet confirmed")
            else:
                warnings.append("A POST was claimed, but server acceptance is unknown; no retry was attempted")
        return ok("send", data, warnings=account_warnings + warnings)
    except SendSafetyError as exc:
        return _safety_failure("send", exc)
    except _attestation.AttestationError as exc:
        return fail("send", [error("conflict", exc.code, False, exc.hint)])
    except _attempts.AttemptConflict as exc:
        return fail("send", [error("conflict", "ATTEMPT_CONFLICT", False, str(exc))])
    except Exception as exc:
        return fail("send", [classify_exception(exc)])
