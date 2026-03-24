"""Draft operations — create, read, discard, attach."""
from __future__ import annotations

import base64
import copy
import html as html_mod
import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import _auth, _config, _local, thread as _thread
from ._envelope import classify_exception, fail, ok

# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def _draft_id() -> str:
    return f"draft00{uuid.uuid4().hex[-14:]}"


def _rfc822_id() -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    value = min(max(int(time.time() * 1000), 36**7), 36**8 - 1)
    encoded = ""
    while value:
        value, rem = divmod(value, 36)
        encoded = chars[rem] + encoded
    return f"<{encoded}.{uuid.uuid4()}@we.are.superhuman.com>"


# ---------------------------------------------------------------------------
# Contact helpers
# ---------------------------------------------------------------------------


def _default_from() -> dict[str, str]:
    return {"email": _config.api("email"), "name": _config.api("author_name")}


def _normalize_contact(contact: dict[str, str] | str) -> dict[str, str]:
    if isinstance(contact, str):
        return {"email": contact.strip(), "name": contact.strip()}
    email = str(contact.get("email", "")).strip()
    name = str(contact.get("name", "")).strip()
    result: dict[str, str] = {}
    if email:
        result["email"] = email
    if name:
        result["name"] = name
    elif email:
        result["name"] = email
    return result


def _normalize_contacts(contacts: list[Any] | None) -> list[dict[str, str]]:
    return [c for c in (_normalize_contact(x) for x in (contacts or [])) if c.get("email")]


def _contact_from_msg(value: dict[str, Any] | None) -> dict[str, str] | None:
    if not value:
        return None
    email = str(value.get("email", "")).strip()
    name = str(value.get("name", "")).strip()
    result: dict[str, str] = {}
    if email:
        result["email"] = email
    if name:
        result["name"] = name
    return result or None


def _contact_to_backend(contact: dict[str, str] | str | None) -> str | None:
    """Format a contact dict as 'Name <email>' string for the backend."""
    if not contact:
        return None
    if isinstance(contact, str):
        return contact
    email = str(contact.get("email", "")).strip()
    name = str(contact.get("name", "")).strip()
    if name and email:
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}" <{email}>'
    return email or name or None


def _dedupe(contacts: list[dict[str, str]], me: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    me_lower = me.lower().strip()
    for c in contacts:
        email = str(c.get("email", "")).strip().lower()
        if not email or email == me_lower or email in seen:
            continue
        seen.add(email)
        result.append(c)
    return result


def _reply_targets(last: dict[str, Any], reply_all: bool, me: str) -> tuple[list, list]:
    sender = _contact_from_msg(last.get("from"))
    if not sender or not sender.get("email"):
        raise RuntimeError("Last message has no sender")

    sender_email = sender.get("email", "").lower()
    me_lower = me.lower().strip()

    # If the last visible message was sent by us, this is a follow-up. Reply to the
    # original recipients instead of replying to ourselves.
    if sender_email == me_lower:
        to_list = _dedupe([_contact_from_msg(entry) for entry in list(last.get("to", []) or []) if _contact_from_msg(entry)], me)
        cc_list: list[dict[str, str]] = []
        if reply_all:
            cc_list = _dedupe([_contact_from_msg(entry) for entry in list(last.get("cc", []) or []) if _contact_from_msg(entry)], me)
        return to_list, cc_list

    to_list: list[dict[str, str]] = [sender]
    cc_list: list[dict[str, str]] = []
    if reply_all:
        extras = []
        for entry in list(last.get("to", []) or []) + list(last.get("cc", []) or []):
            c = _contact_from_msg(entry)
            if c and c.get("email") and c["email"].lower() != sender_email:
                extras.append(c)
        cc_list = _dedupe(extras, me)
    return to_list, cc_list


# ---------------------------------------------------------------------------
# Reply-message resolution — skip system & internal-only messages
# ---------------------------------------------------------------------------

_SYSTEM_DOMAIN = "superhuman.com"


def _is_system_sender(msg: dict[str, Any]) -> bool:
    """True if the message sender is a Superhuman system address."""
    email = str((msg.get("from") or {}).get("email", "")).strip().lower()
    return email.endswith(f"@{_SYSTEM_DOMAIN}")


def _internal_domain() -> str | None:
    """Derive the internal email domain from the configured account email."""
    try:
        email = _config.api("email")
        _, domain = email.rsplit("@", 1)
        return domain.lower()
    except Exception:
        return None


def _msg_participants(msg: dict[str, Any]) -> list[str]:
    """Collect all participant emails (from + to + cc) from a raw message."""
    emails: list[str] = []
    sender = (msg.get("from") or {}).get("email", "")
    if sender:
        emails.append(str(sender).strip().lower())
    for field in ("to", "cc"):
        for entry in list(msg.get(field, []) or []):
            email = str((entry if isinstance(entry, dict) else {}).get("email", "")).strip().lower()
            if email:
                emails.append(email)
    return emails


def _is_internal_only(msg: dict[str, Any], domain: str) -> bool:
    """True if every participant on this message is on the internal domain."""
    participants = _msg_participants(msg)
    return bool(participants) and all(p.endswith(f"@{domain}") for p in participants)


def _thread_has_external(messages: list[dict[str, Any]], domain: str) -> bool:
    """True if any non-system message in the thread has an external participant."""
    for msg in messages:
        if _is_system_sender(msg):
            continue
        for email in _msg_participants(msg):
            if not email.endswith(f"@{domain}") and not email.endswith(f"@{_SYSTEM_DOMAIN}"):
                return True
    return False


def _find_reply_message(messages: list[dict[str, Any]], me: str | None = None) -> dict[str, Any]:
    """Walk messages backwards to find the right message for reply recipient targeting.

    Skips:
    1. Superhuman system messages (*@superhuman.com)
    2. Internal-only messages in threads that have external participants
    3. Self-sent messages (prefer replying to someone else's message)

    Falls back to the last non-system message, then the raw last message.
    """
    if not messages:
        raise RuntimeError("Thread has no messages")

    domain = _internal_domain()
    has_external = domain and _thread_has_external(messages, domain)
    me_lower = (me or "").lower().strip()

    last_non_system: dict[str, Any] | None = None
    last_visible_non_system: dict[str, Any] | None = None
    for msg in reversed(messages):
        if _is_system_sender(msg):
            continue
        if last_non_system is None:
            last_non_system = msg
        # In external threads, skip internal-only messages
        if has_external and domain and _is_internal_only(msg, domain):
            continue
        if last_visible_non_system is None:
            last_visible_non_system = msg
        # Prefer messages from someone else
        sender = str((msg.get("from") or {}).get("email", "")).strip().lower()
        if me_lower and sender == me_lower:
            continue
        return msg

    # Fall back to the last visible non-system message, then any non-system, then raw last
    return last_visible_non_system or last_non_system or messages[-1]


def _find_threading_message(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Walk messages backwards to find the right message for threading headers.

    Skips:
    1. Superhuman system messages (*@superhuman.com)
    2. Internal-only messages in threads that have external participants

    Unlike _find_reply_message, this does NOT skip self-sent messages — follow-ups
    should still thread off your own last externally-visible message when nobody has
    replied yet.

    Falls back to the last non-system message, then the raw last message.
    """
    if not messages:
        raise RuntimeError("Thread has no messages")

    domain = _internal_domain()
    has_external = domain and _thread_has_external(messages, domain)

    last_non_system: dict[str, Any] | None = None
    for msg in reversed(messages):
        if _is_system_sender(msg):
            continue
        if last_non_system is None:
            last_non_system = msg
        if has_external and domain and _is_internal_only(msg, domain):
            continue
        return msg

    return last_non_system or messages[-1]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _to_backend_time(value: Any) -> Any:
    if not value:
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, str):
        try:
            if value.endswith("Z"):
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                return value
            return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        except ValueError:
            return value
    return value


def _text_to_html(text: str) -> str:
    return f"<div>{html_mod.escape(text).replace(chr(10), '<br>')}</div>"


def _mailbox_tzinfo() -> Any:
    try:
        return ZoneInfo(_config.timezone())
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc



def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    mailbox_tz = _mailbox_tzinfo()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=mailbox_tz)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, mailbox_tz)
    if isinstance(value, str):
        try:
            if value.endswith("Z"):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=mailbox_tz)
        except ValueError:
            return None
    return None


def _forward_time_html(value: Any) -> str:
    dt = _parse_datetime(value)
    if not dt:
        return ""

    iso_value = value if isinstance(value, str) else _to_backend_time(value)
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    tz_name = dt.tzname() or "UTC"
    if tz_name == "UTC":
        tz_name = "GMT"
    display = f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day} {dt.year} at {hour}:{dt.minute:02d} {ampm} {tz_name}"
    return f'<time dateTime="{html_mod.escape(str(iso_value))}" class="DateTime">{html_mod.escape(display)}</time>'


def _forward_contact_text(contact: dict[str, Any] | str | None) -> str:
    if not contact:
        return ""
    normalized = _normalize_contact(contact)
    email = normalized.get("email", "")
    name = normalized.get("name", "")
    if email and name and name.lower() != email.lower():
        return f"{html_mod.escape(name)} &lt;{html_mod.escape(email)}&gt;"
    return html_mod.escape(email or name)


def _forward_contacts_text(contacts: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for contact in contacts or []:
        rendered = _forward_contact_text(contact)
        if rendered:
            parts.append(rendered)
    return ", ".join(parts)


def _html_has_inline_cid_refs(html: str) -> bool:
    lowered = html.lower()
    return "cid:" in lowered or "src-cid=" in lowered



def _body_html_from_value(body: Any) -> str:
    if isinstance(body, dict):
        html = str(body.get("html", "")).strip()
        if html and not _html_has_inline_cid_refs(html):
            return html
        text = str(body.get("text", "")).strip()
        if text:
            return _text_to_html(text)
    elif isinstance(body, str) and body.strip():
        html = body.strip()
        if not _html_has_inline_cid_refs(html):
            return _text_to_html(html)
    return ""


def _forward_body_html(raw_message: dict[str, Any], rendered_message: dict[str, Any] | None = None) -> str:
    rendered_html = _body_html_from_value((rendered_message or {}).get("body"))
    if rendered_html:
        return rendered_html

    raw_html = _body_html_from_value(raw_message.get("body"))
    if raw_html:
        return raw_html

    fallback = str(raw_message.get("snippet", "")).strip()
    return _text_to_html(fallback) if fallback else "<div></div>"


def _build_forward_quoted_content(raw_message: dict[str, Any], rendered_message: dict[str, Any] | None = None) -> str:
    from_text = _forward_contact_text(raw_message.get("from"))
    date_html = _forward_time_html(raw_message.get("date"))
    subject = html_mod.escape(str(raw_message.get("subject", "")).strip())
    to_text = _forward_contacts_text(list(raw_message.get("to", []) or []))
    cc_text = _forward_contacts_text(list(raw_message.get("cc", []) or []))
    body_html = _forward_body_html(raw_message, rendered_message)

    lines = ["---------- Forwarded message ----------<br/>"]
    if from_text:
        lines.append(f"From: {from_text}<br/>")
    if date_html:
        lines.append(f"Date: {date_html}<br/>")
    if subject:
        lines.append(f"Subject: {subject}<br/>")
    if to_text:
        lines.append(f"To: {to_text}<br/>")
    if cc_text:
        lines.append(f"<span>Cc: {cc_text}<br/></span>")

    return f"<div><div>{''.join(lines)}</div><br/><div>{body_html}</div></div>"


def _find_forward_message(messages: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    if not messages:
        raise RuntimeError("Thread has no messages")
    for index in range(len(messages) - 1, -1, -1):
        if not _is_system_sender(messages[index]):
            return index, messages[index]
    return len(messages) - 1, messages[-1]


def _inline_quoted_content(body_html: str, quoted_content: str) -> str:
    if not quoted_content:
        return body_html
    return f'{body_html}<br><div class="sh-quoted-content sh-color-black sh-color">{quoted_content}</div>'


def _snippet(text: str) -> str:
    return " ".join(text.split())[:200]


def _fingerprint(to: list, cc: list, atts: list | None = None) -> dict[str, str]:
    return {
        "to": "".join(str(c.get("email", "")) for c in to),
        "cc": "".join(str(c.get("email", "")) for c in cc),
        "attachments": "".join(sorted(str(a.get("uuid", "")) for a in (atts or []))),
    }


def _to_backend(draft: dict[str, Any]) -> dict[str, Any]:
    """Convert app-format draft to backend write format."""
    result = copy.deepcopy(draft)
    result["clientCreatedAt"] = _to_backend_time(result.get("clientCreatedAt"))
    if "from" in result:
        result["from"] = _contact_to_backend(result.get("from"))
    for field in ("to", "cc", "bcc"):
        if result.get(field):
            result[field] = [x for x in (_contact_to_backend(c) for c in result[field]) if x]
    result.pop("attachments", None)
    return result


# ---------------------------------------------------------------------------
# Core write
# ---------------------------------------------------------------------------


def _write_userdata_message(writes: list[dict[str, Any]], history_id: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"writes": writes}
    if history_id is not None:
        payload["currentHistoryId"] = history_id
    req = urllib.request.Request(
        "https://mail.superhuman.com/~backend/v3/userdata.writeMessage",
        data=json.dumps(payload).encode(),
        headers=_auth.api_headers(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _write_draft(draft: dict[str, Any], command: str = "draft.create") -> dict[str, Any]:
    """Persist a draft object and return an envelope result."""
    thread_id = str(draft["threadId"])
    draft_id = str(draft["id"])
    cmd = command
    try:
        history_id = _thread.current_history_id(thread_id)
        result = _write_userdata_message(
            [{"path": f"users/{_config.api('google_id')}/threads/{thread_id}/messages/{draft_id}/draft", "value": _to_backend(draft)}],
            history_id=history_id,
        )
        return ok(cmd, {
            "draft_id": draft_id,
            "thread_id": thread_id,
            "history_id": int(result.get("currentHistoryId", 0) or 0),
            "action": draft.get("action", "compose"),
            "subject": draft.get("subject", ""),
            "to": draft.get("to", []),
            "cc": draft.get("cc", []),
        })
    except Exception as e:
        return fail(cmd, [classify_exception(e)])


# ---------------------------------------------------------------------------
# Smart-send field helpers
# ---------------------------------------------------------------------------


def _apply_smart_send(
    draft: dict[str, Any],
    *,
    scheduled_for: str | None = None,
    abort_on_reply: bool = False,
    reminder: str | None = None,
    sensitivity_label_id: str | None = None,
    sensitivity_tenant_id: str | None = None,
) -> None:
    """Set optional smart-send fields on a draft dict (mutates in place)."""
    if scheduled_for:
        draft["scheduledFor"] = scheduled_for
    if abort_on_reply:
        draft["abortOnReply"] = True
    if reminder:
        draft["reminder"] = reminder
    if sensitivity_label_id:
        draft["sensitivityLabelId"] = sensitivity_label_id
    if sensitivity_tenant_id:
        draft["sensitivityTenantId"] = sensitivity_tenant_id


# ---------------------------------------------------------------------------
# Public API — draft creation
# ---------------------------------------------------------------------------


def create_reply(
    thread_id: str,
    body: str,
    *,
    body_html: str | None = None,
    reply_all: bool = False,
    scheduled_for: str | None = None,
    abort_on_reply: bool = False,
    reminder: str | None = None,
    sensitivity_label_id: str | None = None,
    sensitivity_tenant_id: str | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Create a reply or reply-all draft on an existing thread."""
    try:
        local = _local.get_thread_json(thread_id, account)
        messages = list(local.get("messages", []) or [])
        if not messages:
            raise RuntimeError(f"Thread has no messages: {thread_id}")

        thread_msg = _find_threading_message(messages)
        from_contact = _default_from()
        reply_msg = _find_reply_message(messages, me=from_contact["email"])
        to_list, cc_list = _reply_targets(reply_msg, reply_all, from_contact["email"])
        did = _draft_id()
        now_ms = int(time.time() * 1000)

        draft: dict[str, Any] = {
            "id": did,
            "threadId": thread_id,
            "action": "reply-all" if reply_all else "reply",
            "from": from_contact,
            "to": to_list,
            "cc": cc_list,
            "bcc": [],
            "subject": _reply_subject(str(thread_msg.get("subject", ""))),
            "body": body_html or _text_to_html(body),
            "snippet": _snippet(body),
            "inReplyTo": str(thread_msg.get("id", "")) or None,
            "inReplyToRfc822Id": thread_msg.get("rfc822Id") or None,
            "labelIds": ["DRAFT"],
            "clientCreatedAt": now_ms,
            "date": _iso_now(),
            "fingerprint": _fingerprint(to_list, cc_list),
            "lastSessionId": str(uuid.uuid4()),
            "quotedContent": "",
            "quotedContentInlined": False,
            "references": [thread_msg["rfc822Id"]] if thread_msg.get("rfc822Id") else [],
            "rfc822Id": _rfc822_id(),
            "schemaVersion": 3,
            "attachments": [],
            "totalComposeSeconds": 0,
            "timeZone": _config.timezone(),
        }
        _apply_smart_send(
            draft,
            scheduled_for=scheduled_for,
            abort_on_reply=abort_on_reply,
            reminder=reminder,
            sensitivity_label_id=sensitivity_label_id,
            sensitivity_tenant_id=sensitivity_tenant_id,
        )
        command = "draft.reply-all" if reply_all else "draft.reply"
        return _write_draft(draft, command)
    except Exception as e:
        command = "draft.reply-all" if reply_all else "draft.reply"
        return fail(command, [classify_exception(e)])


def create_forward(
    thread_id: str,
    body: str,
    *,
    to: list[Any] | None = None,
    cc: list[Any] | None = None,
    bcc: list[Any] | None = None,
    body_html: str | None = None,
    scheduled_for: str | None = None,
    abort_on_reply: bool = False,
    reminder: str | None = None,
    sensitivity_label_id: str | None = None,
    sensitivity_tenant_id: str | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Create a forward draft on an existing thread."""
    try:
        local = _local.get_thread_json(thread_id, account)
        messages = list(local.get("messages", []) or [])
        if not messages:
            raise RuntimeError(f"Thread has no messages: {thread_id}")

        forward_index, forward_message = _find_forward_message(messages)
        try:
            rendered_messages = _local.get_messages(thread_id, account)
            rendered_last = rendered_messages[forward_index] if forward_index < len(rendered_messages) else None
        except Exception:
            rendered_last = None

        quoted_content = _build_forward_quoted_content(forward_message, rendered_last)
        did = _draft_id()
        now_ms = int(time.time() * 1000)
        to_list = _normalize_contacts(to)
        cc_list = _normalize_contacts(cc)
        bcc_list = _normalize_contacts(bcc)
        composed_body = body_html or _text_to_html(body)

        draft: dict[str, Any] = {
            "id": did,
            "threadId": thread_id,
            "action": "forward",
            "from": _default_from(),
            "to": to_list,
            "cc": cc_list,
            "bcc": bcc_list,
            "subject": _forward_subject(str(forward_message.get("subject", ""))),
            "body": _inline_quoted_content(composed_body, quoted_content),
            "snippet": _snippet(body),
            "inReplyTo": str(forward_message.get("id", "")) or None,
            "inReplyToRfc822Id": forward_message.get("rfc822Id") or None,
            "labelIds": ["DRAFT"],
            "clientCreatedAt": now_ms,
            "date": _iso_now(),
            "fingerprint": _fingerprint(to_list, cc_list),
            "lastSessionId": str(uuid.uuid4()),
            "quotedContent": quoted_content,
            "quotedContentInlined": bool(quoted_content),
            "references": [forward_message["rfc822Id"]] if forward_message.get("rfc822Id") else [],
            "rfc822Id": _rfc822_id(),
            "schemaVersion": 3,
            "attachments": [],
            "totalComposeSeconds": 0,
            "timeZone": _config.timezone(),
        }
        _apply_smart_send(
            draft,
            scheduled_for=scheduled_for,
            abort_on_reply=abort_on_reply,
            reminder=reminder,
            sensitivity_label_id=sensitivity_label_id,
            sensitivity_tenant_id=sensitivity_tenant_id,
        )
        return _write_draft(draft, "draft.forward")
    except Exception as e:
        return fail("draft.forward", [classify_exception(e)])


def create_compose(
    subject: str,
    body: str,
    *,
    to: list[Any] | None = None,
    cc: list[Any] | None = None,
    bcc: list[Any] | None = None,
    body_html: str | None = None,
    scheduled_for: str | None = None,
    abort_on_reply: bool = False,
    reminder: str | None = None,
    sensitivity_label_id: str | None = None,
    sensitivity_tenant_id: str | None = None,
) -> dict[str, Any]:
    """Create a new compose draft (new thread)."""
    try:
        tid = _draft_id()
        did = _draft_id()
        to_list = _normalize_contacts(to)
        cc_list = _normalize_contacts(cc)
        bcc_list = _normalize_contacts(bcc)
        now_ms = int(time.time() * 1000)

        draft: dict[str, Any] = {
            "id": did,
            "threadId": tid,
            "action": "compose",
            "from": _default_from(),
            "to": to_list,
            "cc": cc_list,
            "bcc": bcc_list,
            "subject": subject,
            "body": body_html or _text_to_html(body),
            "snippet": _snippet(body),
            "labelIds": ["DRAFT"],
            "clientCreatedAt": now_ms,
            "date": _iso_now(),
            "fingerprint": _fingerprint(to_list, cc_list),
            "lastSessionId": str(uuid.uuid4()),
            "quotedContent": "",
            "quotedContentInlined": False,
            "references": [],
            "rfc822Id": _rfc822_id(),
            "schemaVersion": 3,
            "attachments": [],
            "totalComposeSeconds": 0,
            "timeZone": _config.timezone(),
        }
        _apply_smart_send(
            draft,
            scheduled_for=scheduled_for,
            abort_on_reply=abort_on_reply,
            reminder=reminder,
            sensitivity_label_id=sensitivity_label_id,
            sensitivity_tenant_id=sensitivity_tenant_id,
        )
        return _write_draft(draft, "draft.compose")
    except Exception as e:
        return fail("draft.compose", [classify_exception(e)])


# ---------------------------------------------------------------------------
# Read / discard / attach
# ---------------------------------------------------------------------------


def read(thread_id: str, draft_id: str | None = None) -> dict[str, Any]:
    """Read draft(s) from thread userdata."""
    try:
        ud = _thread.userdata_raw(thread_id)
        if not ud:
            return ok("draft.read", {"thread_id": thread_id, "drafts": []})
        msgs = ud.get("messages", {})
        drafts: list[dict[str, Any]] = []
        for mid, msg_data in msgs.items():
            d = msg_data.get("draft")
            if d and not msg_data.get("discardedAt"):
                if draft_id is None or mid == draft_id:
                    drafts.append(d)
        return ok("draft.read", {"thread_id": thread_id, "draft_count": len(drafts), "drafts": drafts})
    except Exception as e:
        return fail("draft.read", [classify_exception(e)])


def discard(thread_id: str, draft_id: str) -> dict[str, Any]:
    """Discard (soft-delete) a draft."""
    try:
        history_id = _thread.current_history_id(thread_id)
        _write_userdata_message(
            [{"path": f"users/{_config.api('google_id')}/threads/{thread_id}/messages/{draft_id}/discardedAt", "value": _iso_now()}],
            history_id=history_id,
        )
        return ok("draft.discard", {"thread_id": thread_id, "draft_id": draft_id})
    except Exception as e:
        return fail("draft.discard", [classify_exception(e)])


def attach(
    thread_id: str,
    draft_id: str,
    filepath: str,
    *,
    content_type: str = "application/octet-stream",
    inline: bool = False,
) -> dict[str, Any]:
    """Upload a file and attach it to a draft."""
    try:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        content = path.read_bytes()
        att_uuid = str(uuid.uuid4())

        # Step 1: upload bytes
        upload_payload = {
            "draftMessageId": draft_id,
            "threadId": thread_id,
            "uuid": att_uuid,
            "contentType": content_type,
            "content": base64.b64encode(content).decode(),
            "teamId": None,
        }
        req = urllib.request.Request(
            "https://mail.superhuman.com/~backend/v3/attachments.upload",
            data=json.dumps(upload_payload).encode(),
            headers=_auth.api_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            upload_result = json.loads(resp.read())

        # Step 2: persist attachment metadata
        history_id = _thread.current_history_id(thread_id)
        att_meta = {
            "uuid": att_uuid,
            "name": path.name,
            "type": content_type,
            "messageId": draft_id,
            "threadId": thread_id,
            "inline": inline,
            "source": {
                "type": "upload-firebase",
                "threadId": thread_id,
                "messageId": draft_id,
                "uuid": att_uuid,
                "url": upload_result.get("downloadUrl", ""),
            },
            "discardedAt": None,
            "createdAt": _iso_now(),
            "size": len(content),
        }
        write_result = _write_userdata_message(
            [{"path": f"users/{_config.api('google_id')}/threads/{thread_id}/messages/{draft_id}/attachments/{att_uuid}", "value": att_meta}],
            history_id=history_id,
        )
        return ok("draft.attach", {
            "thread_id": thread_id,
            "draft_id": draft_id,
            "attachment_uuid": att_uuid,
            "filename": path.name,
            "size_bytes": len(content),
            "history_id": int(write_result.get("currentHistoryId", 0) or 0),
        })
    except Exception as e:
        return fail("draft.attach", [classify_exception(e)])


# ---------------------------------------------------------------------------
# Subject helpers
# ---------------------------------------------------------------------------


def _reply_subject(subject: str) -> str:
    clean = (subject or "").strip()
    return clean if clean.lower().startswith("re:") else f"Re: {clean}"


def _forward_subject(subject: str) -> str:
    clean = (subject or "").strip()
    return clean if clean.lower().startswith("fwd:") else f"Fwd: {clean}"
