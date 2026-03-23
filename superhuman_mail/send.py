"""Send operations — validate and execute.

Send is IRREVERSIBLE. The CLI requires --dry-run or --confirm.
"""
from __future__ import annotations

import json
import time
import urllib.request
import uuid
from email.utils import parseaddr
from typing import Any

from . import _auth, _config, thread as _thread
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
        "reminder": draft.get("reminder"),
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


def validate(thread_id: str, draft_id: str) -> dict[str, Any]:
    """Dry-run: read the draft and validate it's sendable.

    Returns draft details and any issues, WITHOUT actually sending.
    """
    try:
        ud = _thread.userdata_raw(thread_id)
        if not ud:
            return fail("send.validate", [error("not-found", "THREAD_NOT_FOUND", False, f"No userdata for thread {thread_id}")])

        msgs = ud.get("messages", {})
        msg_data = msgs.get(draft_id, {})
        draft = msg_data.get("draft")
        if not draft:
            return fail("send.validate", [error("not-found", "DRAFT_NOT_FOUND", False, f"Draft {draft_id} not found on thread {thread_id}")])

        if msg_data.get("discardedAt"):
            return fail("send.validate", [error("conflict", "DRAFT_DISCARDED", False, "Draft has been discarded")])

        # Validate required fields
        warnings: list[str] = []
        to_list = draft.get("to") or []
        if not to_list:
            warnings.append("Draft has no recipients (to is empty)")
        if not (draft.get("subject") or "").strip():
            warnings.append("Draft has no subject")
        if not (draft.get("body") or draft.get("htmlBody") or "").strip():
            warnings.append("Draft has no body")

        outgoing = _build_outgoing(draft)

        return ok("send.validate", {
            "thread_id": thread_id,
            "draft_id": draft_id,
            "sendable": len(warnings) == 0 or bool(to_list),
            "action": draft.get("action", "compose"),
            "from": outgoing.get("from"),
            "to": outgoing.get("to"),
            "cc": outgoing.get("cc"),
            "subject": outgoing.get("subject"),
            "body_preview": (draft.get("snippet") or "")[:200],
            "has_attachments": bool(outgoing.get("attachments")),
            "scheduled_for": draft.get("scheduledFor"),
        }, warnings=warnings)
    except Exception as e:
        return fail("send.validate", [classify_exception(e)])


def execute(thread_id: str, draft_id: str, *, delay: int = SEND_DELAY_SECONDS) -> dict[str, Any]:
    """Actually send a draft. THIS IS IRREVERSIBLE.

    The draft must already exist on the thread. Use validate() first.
    """
    try:
        ud = _thread.userdata_raw(thread_id)
        if not ud:
            return fail("send", [error("not-found", "THREAD_NOT_FOUND", False, f"No userdata for thread {thread_id}")])

        msgs = ud.get("messages", {})
        msg_data = msgs.get(draft_id, {})
        draft = msg_data.get("draft")
        if not draft:
            return fail("send", [error("not-found", "DRAFT_NOT_FOUND", False, f"Draft {draft_id} not found")])

        outgoing = _build_outgoing(draft)
        request_body = {
            "version": 3,
            "outgoing_message": outgoing,
            "delay": delay,
            "is_multi_recipient": True,
        }

        req = urllib.request.Request(
            "https://mail.superhuman.com/~backend/messages/send",
            data=json.dumps(request_body).encode(),
            headers={**_auth.api_headers(), "Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            try:
                result = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                result = {}

        return ok("send", {
            "thread_id": thread_id,
            "draft_id": draft_id,
            "delay_seconds": delay,
            "to": outgoing.get("to"),
            "subject": outgoing.get("subject"),
            "sent": True,
        })
    except Exception as e:
        return fail("send", [classify_exception(e)])
