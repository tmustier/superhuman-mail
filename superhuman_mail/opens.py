"""Read receipts / opens helpers."""
from __future__ import annotations

from typing import Any

from . import _local
from ._envelope import classify_exception, fail, ok
from .thread import userdata_raw


def _latest_read_at(reads: dict[str, list[dict[str, Any]]]) -> str:
    latest = ""
    for events in reads.values():
        for event in events or []:
            read_at = str(event.get("readAt") or "")
            if read_at > latest:
                latest = read_at
    return latest


def per_thread(thread_id: str, recipient: str | None = None) -> dict[str, Any]:
    """Read per-message read statuses / read receipts for a thread."""
    try:
        ud = userdata_raw(thread_id)
        if not ud:
            return ok("opens", {
                "thread_id": thread_id,
                "source": "userdata.read",
                "history_id": None,
                "container_updated_at": None,
                "message_count_with_reads": 0,
                "recipient_count": 0,
                "messages": [],
                "by_recipient": {},
            }, warnings=["No thread userdata found"])

        recipient_lower = recipient.lower().strip() if recipient else None
        msgs = ud.get("messages", {})
        messages: list[dict[str, Any]] = []
        by_recipient: dict[str, list[dict[str, Any]]] = {}

        for message_id, msg_data in msgs.items():
            reads = msg_data.get("reads") or {}
            if not reads:
                continue
            if recipient_lower:
                reads = {
                    email: events
                    for email, events in reads.items()
                    if email.lower().strip() == recipient_lower
                }
                if not reads:
                    continue

            latest_read_at = _latest_read_at(reads)
            read_event_count = sum(len(events or []) for events in reads.values())
            message_entry = {
                "message_id": message_id,
                "history_id": msg_data.get("historyId"),
                "reads_shared_by": msg_data.get("readsSharedBy"),
                "recipient_count": len(reads),
                "read_event_count": read_event_count,
                "latest_read_at": latest_read_at or None,
                "reads": reads,
            }
            messages.append(message_entry)

            for recipient_email, events in reads.items():
                items = by_recipient.setdefault(recipient_email, [])
                for event in events or []:
                    items.append({
                        "message_id": message_id,
                        "device": event.get("device"),
                        "read_at": event.get("readAt"),
                        "reads_shared_by": msg_data.get("readsSharedBy"),
                    })

        messages.sort(key=lambda item: (item.get("latest_read_at") or "", item["message_id"]), reverse=True)
        for recipient_email in by_recipient:
            by_recipient[recipient_email].sort(key=lambda item: (item.get("read_at") or "", item["message_id"]), reverse=True)

        warnings: list[str] = []
        if not messages:
            if recipient:
                warnings.append(f"No read-status data found for recipient {recipient}")
            else:
                warnings.append("No read-status data found on this thread")

        return ok("opens", {
            "thread_id": thread_id,
            "source": "userdata.read",
            "history_id": ud.get("historyId"),
            "container_updated_at": ud.get("containerUpdatedAt"),
            "message_count_with_reads": len(messages),
            "recipient_count": len(by_recipient),
            "messages": messages,
            "by_recipient": by_recipient,
        }, warnings=warnings)
    except Exception as e:
        return fail("opens", [classify_exception(e)])


def recent(
    *,
    limit: int = 20,
    recipient: str | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Read recent opens from the local activity_feed table."""
    try:
        events = _local.recent_opens(limit=limit, recipient=recipient, account=account)
        warnings: list[str] = []
        if not events:
            if recipient:
                warnings.append(f"No recent opens found for {recipient}")
            else:
                warnings.append("No recent opens in local cache")
        return ok("opens.recent", {
            "source": "local-db.activity_feed",
            "limit": limit,
            "returned": len(events),
            "events": events,
        }, warnings=warnings)
    except Exception as e:
        return fail("opens.recent", [classify_exception(e)])
