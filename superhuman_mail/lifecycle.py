"""Canonical Superhuman draft/send lifecycle classification.

A source draft is not outbound evidence.  This module joins the message-level
userdata wrapper (including ``sendJob``) with immutable provider messages from
Superhuman's local cache and returns an explicit, provenance-bearing state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from . import _config, _local, thread as _thread

ACTIVE = "active_draft"
SCHEDULED = "scheduled"
REQUESTED = "send_requested"
PENDING_UNDO = "send_pending_undo"
FAILED = "send_failed"
ABORTED = "send_aborted"
DISCARDED = "discarded"
BACKEND_CONFIRMED = "sent_backend_confirmed"
PROVIDER_CONFIRMED = "sent_provider_confirmed"
INCONSISTENT = "inconsistent"

TERMINAL_STATES = {
    FAILED,
    ABORTED,
    DISCARDED,
    BACKEND_CONFIRMED,
    PROVIDER_CONFIRMED,
    INCONSISTENT,
}
BLOCKING_STATES = TERMINAL_STATES | {SCHEDULED, REQUESTED, PENDING_UNDO}


class AccountBindingError(ValueError):
    """The requested mailbox cannot be bound to the configured API identity."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # Superhuman timestamps are normally epoch milliseconds.
        seconds = float(value) / 1000 if abs(float(value)) > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    parsed = _parse_time(value)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z") if parsed else None


def resolve_account(account: str | None = None, *, require_explicit: bool = False) -> tuple[dict[str, str], list[str]]:
    """Resolve a mailbox to the immutable API user identity.

    The current private API credentials are bound to one Google/provider user.
    Local DB discovery can list additional mailboxes, but combining one of those
    DBs with the configured userdata credential would be unsafe.  Fail rather
    than guess.
    """
    cfg = _config.load()
    api_cfg = cfg.get("superhuman_api", {})
    configured_email = str(api_cfg.get("email") or cfg.get("email_account") or "").strip()
    provider_user_id = str(api_cfg.get("google_id") or "").strip()
    if not configured_email or not provider_user_id:
        raise AccountBindingError("Configured Superhuman account is missing email or immutable provider user ID")

    accounts = [str(item.get("email", "")).strip() for item in cfg.get("superhuman", {}).get("accounts", [])]
    accounts = [item for item in accounts if item]
    warnings: list[str] = []

    if account is None:
        if require_explicit and len({item.lower() for item in accounts}) > 1:
            raise AccountBindingError("Multiple Superhuman accounts are configured; --account is required")
        account = configured_email
        if require_explicit:
            warnings.append(f"No --account supplied; bound to configured account {configured_email}")

    if account.strip().lower() != configured_email.lower():
        raise AccountBindingError(
            f"Account {account} is not bound to the configured API identity {configured_email}; run setup for that account"
        )

    return {"email": configured_email, "provider_user_id": provider_user_id}, warnings


def _field(obj: dict[str, Any] | None, *names: str) -> Any:
    source = obj or {}
    for name in names:
        if name in source and source[name] not in (None, ""):
            return source[name]
    return None


def _labels(message: dict[str, Any]) -> set[str]:
    return {str(item).upper() for item in (message.get("labelIds") or message.get("labels") or [])}


def _sender_email(message: dict[str, Any]) -> str:
    sender = message.get("from") or message.get("sender") or {}
    if isinstance(sender, dict):
        return str(sender.get("email") or sender.get("emailAddress") or "").strip().lower()
    return str(sender).strip().lower()


def _message_id(message: dict[str, Any]) -> str:
    return str(message.get("id") or message.get("messageId") or "")


def _provider_draft_id(message: dict[str, Any]) -> str | None:
    value = _field(message, "superhumanOwnDraftId", "superhumanDraftId", "superhuman_own_draft_id")
    return str(value) if value is not None else None


def _provider_superhuman_id(message: dict[str, Any]) -> str | None:
    value = _field(message, "superhumanId", "superhuman_id")
    return str(value) if value is not None else None


def _provider_proof(
    messages: Iterable[dict[str, Any]],
    *,
    account_email: str,
    draft_id: str,
    send_job: dict[str, Any],
    attempt_superhuman_id: str | None,
) -> tuple[dict[str, Any] | None, bool]:
    """Return an unambiguously linked SENT provider message and conflict flag."""
    job_message_id = str(_field(send_job, "messageId", "message_id") or "")
    expected_sid = str(_field(send_job, "superhumanId", "superhuman_id") or attempt_superhuman_id or "")
    conflict = False
    candidate: dict[str, Any] | None = None

    for message in messages:
        if "SENT" not in _labels(message) or _sender_email(message) != account_email.lower():
            continue
        mid = _message_id(message)
        linked_draft = _provider_draft_id(message)
        linked_sid = _provider_superhuman_id(message)

        # Proof A: exact immutable provider ID from the completed backend job.
        if job_message_id and mid == job_message_id:
            if linked_draft is not None and linked_draft != draft_id:
                conflict = True
                continue
            if linked_sid is not None and expected_sid and linked_sid != expected_sid:
                conflict = True
                continue
            candidate = candidate or message
            continue

        # Proof B: userdata may lag, but both source-draft and attempt identities link.
        if linked_draft == draft_id and expected_sid and linked_sid == expected_sid:
            candidate = candidate or message
            continue

        # A message that claims either identity but disagrees on the other is evidence
        # of contradictory linkage, not a fuzzy match.
        if linked_draft == draft_id or (expected_sid and linked_sid == expected_sid) or (job_message_id and mid == job_message_id):
            conflict = True

    return candidate, conflict


def _has_any_field(job: dict[str, Any], *names: str) -> bool:
    return any(name in job for name in names)


def _has_failure(job: dict[str, Any]) -> bool:
    return any(bool(_field(job, name)) for name in ("failedAt", "failureAt", "error", "failureReason"))


def _has_abort(job: dict[str, Any]) -> bool:
    return any(
        bool(_field(job, name))
        for name in ("abortedAt", "abortAt", "cancelledAt", "canceledAt", "unscheduledAt")
    )


def _replacement_thread_id(userdata: dict[str, Any], wrapper: dict[str, Any]) -> str | None:
    candidates = [userdata.get("threadReplacement"), wrapper.get("threadReplacement")]
    for item in candidates:
        if isinstance(item, str) and item:
            return item
        if isinstance(item, dict):
            value = _field(item, "movedToThreadId", "threadId", "replacementThreadId")
            if value:
                return str(value)
    job = wrapper.get("sendJob") or {}
    value = _field(job, "threadId", "thread_id")
    return str(value) if value else None


def classify(
    *,
    account: dict[str, str],
    thread_id: str,
    draft_id: str,
    userdata: dict[str, Any],
    provider_messages: Iterable[dict[str, Any]] = (),
    observed_at: str | None = None,
    attempt_superhuman_id: str | None = None,
    provider_thread_id: str | None = None,
) -> dict[str, Any]:
    """Classify one ``(account, thread, draft)`` from already-read evidence."""
    observed_at = observed_at or _now_iso()
    wrapper = (userdata.get("messages") or {}).get(draft_id) or {}
    draft = wrapper.get("draft") or {}
    job = wrapper.get("sendJob") or {}
    provider_messages = list(provider_messages)

    provider, linkage_conflict = _provider_proof(
        provider_messages,
        account_email=account["email"],
        draft_id=draft_id,
        send_job=job,
        attempt_superhuman_id=attempt_superhuman_id,
    )
    has_failure = _has_failure(job)
    has_abort = _has_abort(job)
    sent_at_present = bool(_field(job, "sentAt"))
    backend_success = bool(sent_at_present and _field(job, "messageId", "message_id"))
    partial_terminal_success = (sent_at_present or bool(_field(job, "sendVerifiedAt", "send_verified_at"))) and not backend_success
    contradictory = (
        sum(bool(item) for item in (backend_success, has_failure, has_abort)) > 1
        or linkage_conflict
        or partial_terminal_success
    )
    consistency = "conflicting" if contradictory or (provider is not None and (has_failure or has_abort)) else "matched"

    if provider is not None:
        state = PROVIDER_CONFIRMED
        confidence = "provider_confirmed"
        outbound = True
    elif contradictory:
        state = INCONSISTENT
        confidence = "conflicting"
        outbound = False
    elif backend_success:
        state = BACKEND_CONFIRMED
        confidence = "backend_confirmed"
        outbound = False
    elif has_failure:
        state = FAILED
        confidence = "backend_terminal"
        outbound = False
    elif has_abort:
        state = ABORTED
        confidence = "backend_terminal"
        outbound = False
    elif job:
        send_at = _parse_time(_field(job, "sendAt", "send_at"))
        scheduled_for = _parse_time(_field(draft, "scheduledFor", "scheduled_for"))
        if bool(_field(job, "notSentToServer", "not_sent_to_server")):
            state = REQUESTED
        elif scheduled_for and send_at and send_at > datetime.now(timezone.utc):
            state = SCHEDULED
        elif send_at and send_at > datetime.now(timezone.utc):
            state = PENDING_UNDO
        else:
            state = REQUESTED
        confidence = "backend_nonterminal"
        outbound = False
    elif wrapper.get("sending"):
        # Superhuman sets this optimistic wrapper flag before sendJob is fully
        # materialized. Treat it as blocking native-send evidence.
        state = REQUESTED
        confidence = "userdata_optimistic"
        outbound = False
    elif wrapper.get("discardedAt") or draft.get("discardedAt"):
        state = DISCARDED
        confidence = "userdata"
        outbound = False
    else:
        state = ACTIVE
        confidence = "userdata"
        outbound = False

    provider_data: dict[str, Any] | None = None
    if provider is not None:
        provider_data = {
            "id": _message_id(provider),
            "thread_id": provider_thread_id or thread_id,
            "labels": sorted(_labels(provider)),
            "superhuman_own_draft_id": _provider_draft_id(provider),
            "superhuman_id": _provider_superhuman_id(provider),
            "date": _iso(provider.get("date")),
        }

    return {
        "account": dict(account),
        "thread_id": thread_id,
        "draft_id": draft_id,
        "state": state,
        "terminal": state in TERMINAL_STATES,
        "send_blocked": state in BLOCKING_STATES,
        "outbound_evidence": outbound,
        "confidence": confidence,
        "consistency": consistency,
        "timestamps": {
            "draft_created_at": _iso(_field(draft, "clientCreatedAt", "date")),
            "scheduled_for": _iso(_field(draft, "scheduledFor", "scheduled_for")),
            "send_at": _iso(_field(job, "sendAt", "send_at")),
            "sent_at": _iso(_field(job, "sentAt", "sent_at")),
            "send_verified_at": _iso(_field(job, "sendVerifiedAt", "send_verified_at")),
            "failed_at": _iso(_field(job, "failedAt", "failureAt")),
            "aborted_at": _iso(_field(job, "abortedAt", "cancelledAt", "canceledAt")),
            "provider_message_at": _iso(provider.get("date")) if provider else None,
        },
        "send_job": {
            "message_id": _field(job, "messageId", "message_id"),
            "superhuman_id": _field(job, "superhumanId", "superhuman_id"),
            "not_sent_to_server": bool(_field(job, "notSentToServer", "not_sent_to_server")),
            "not_sent_to_server_present": _has_any_field(job, "notSentToServer", "not_sent_to_server"),
            "present": bool(job),
            "sending": bool(wrapper.get("sending")),
        },
        "provider_message": provider_data,
        "observations": [
            {
                "source": "superhuman_userdata_api",
                "history_id": userdata.get("historyId"),
                "observed_at": observed_at,
            },
            {
                "source": "superhuman_local_cache",
                "provider_thread_id": provider_thread_id or thread_id,
                "message_count": len(provider_messages),
                "observed_at": observed_at,
            },
        ],
    }


def observe_thread(
    thread_id: str,
    *,
    account: str | None = None,
    require_explicit_account: bool = False,
    attempt_ids: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Read userdata/cache once and classify every source draft on a thread."""
    identity, warnings = resolve_account(account, require_explicit=require_explicit_account)
    userdata = _thread.userdata_raw(thread_id, account=identity["email"])
    if not userdata:
        raise LookupError(f"No userdata for thread {thread_id}")

    provider_thread_id = thread_id
    provider_messages: list[dict[str, Any]] = []
    try:
        raw = _local.get_thread_json(thread_id, identity["email"])
        provider_messages.extend(list(raw.get("messages") or []))
    except Exception as exc:
        warnings.append(f"Provider cache unavailable for {thread_id}: {exc}")

    # Synthetic compose threads may be replaced with the immutable provider thread.
    for wrapper in (userdata.get("messages") or {}).values():
        replacement = _replacement_thread_id(userdata, wrapper or {})
        if replacement and replacement != thread_id:
            provider_thread_id = replacement
            try:
                raw = _local.get_thread_json(replacement, identity["email"])
                provider_messages.extend(list(raw.get("messages") or []))
            except Exception as exc:
                warnings.append(f"Replacement provider cache unavailable for {replacement}: {exc}")
            break

    observed_at = _now_iso()
    lifecycle_by_id: dict[str, dict[str, Any]] = {}
    for draft_id, wrapper in (userdata.get("messages") or {}).items():
        if not isinstance(wrapper, dict) or not (
            wrapper.get("draft") or wrapper.get("sendJob") or wrapper.get("discardedAt")
        ):
            continue
        lifecycle_by_id[str(draft_id)] = classify(
            account=identity,
            thread_id=thread_id,
            draft_id=str(draft_id),
            userdata=userdata,
            provider_messages=provider_messages,
            observed_at=observed_at,
            attempt_superhuman_id=(attempt_ids or {}).get(str(draft_id)),
            provider_thread_id=provider_thread_id,
        )

    return {
        "account": identity,
        "thread_id": thread_id,
        "userdata": userdata,
        "provider_thread_id": provider_thread_id,
        "lifecycle_by_draft_id": lifecycle_by_id,
        "observed_at": observed_at,
    }, warnings


def observe(
    thread_id: str,
    draft_id: str,
    *,
    account: str | None = None,
    require_explicit_account: bool = False,
    attempt_superhuman_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Read and classify one draft, returning lifecycle, raw wrapper, warnings."""
    observed, warnings = observe_thread(
        thread_id,
        account=account,
        require_explicit_account=require_explicit_account,
        attempt_ids={draft_id: attempt_superhuman_id} if attempt_superhuman_id else None,
    )
    lifecycle = observed["lifecycle_by_draft_id"].get(draft_id)
    if lifecycle is None:
        raise LookupError(f"Draft {draft_id} not found on thread {thread_id}")
    wrapper = (observed["userdata"].get("messages") or {}).get(draft_id) or {}
    return lifecycle, wrapper, warnings
