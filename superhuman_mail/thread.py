"""Thread operations — read messages and userdata."""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from . import _auth, _config, _local
from ._envelope import classify_exception, fail, ok


def read(thread_id: str, account: str | None = None) -> dict[str, Any]:
    """Read thread messages from the local Superhuman DB."""
    try:
        messages = _local.get_messages(thread_id, account)
        return ok("thread.read", {
            "thread_id": thread_id,
            "message_count": len(messages),
            "messages": messages,
        })
    except Exception as e:
        return fail("thread.read", [classify_exception(e)])


def userdata(thread_id: str) -> dict[str, Any]:
    """Read thread userdata (drafts, comments, metadata) from the API."""
    try:
        payload = {
            "reads": [{"path": f"users/{_config.api('google_id')}/threads/{thread_id}"}],
            "pageSize": 100,
        }
        req = urllib.request.Request(
            "https://mail.superhuman.com/~backend/v3/userdata.read",
            data=json.dumps(payload).encode(),
            headers=_auth.api_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        results = data.get("results", [])
        value = results[0].get("value") if results else None
        return ok("thread.userdata", {"thread_id": thread_id, "userdata": value})
    except Exception as e:
        return fail("thread.userdata", [classify_exception(e)])


def userdata_raw(thread_id: str) -> dict[str, Any] | None:
    """Read thread userdata and return the raw value (no envelope). Used internally."""
    payload = {
        "reads": [{"path": f"users/{_config.api('google_id')}/threads/{thread_id}"}],
        "pageSize": 100,
    }
    req = urllib.request.Request(
        "https://mail.superhuman.com/~backend/v3/userdata.read",
        data=json.dumps(payload).encode(),
        headers=_auth.api_headers(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    results = data.get("results", [])
    return results[0].get("value") if results else None


def current_history_id(thread_id: str) -> int | None:
    """Get the current history ID for a thread (used for optimistic writes)."""
    ud = userdata_raw(thread_id)
    if not ud:
        return None
    hid = ud.get("historyId")
    return int(hid) if hid is not None else None


def list_threads(
    *,
    limit: int = 20,
    unread: bool = False,
    include_participants: bool = False,
    account: str | None = None,
) -> dict[str, Any]:
    """List recent threads from the local DB."""
    try:
        threads = _local.list_threads(
            limit=limit,
            unread=unread,
            include_participants=include_participants,
            account=account,
        )
        return ok("thread.list", {
            "source": "local-db",
            "limit": limit,
            "returned": len(threads),
            "threads": threads,
        })
    except Exception as e:
        return fail("thread.list", [classify_exception(e)])


def search(
    query: str,
    *,
    limit: int = 10,
    unread: bool = False,
    include_participants: bool = False,
    account: str | None = None,
) -> dict[str, Any]:
    """Search threads using the local FTS index."""
    try:
        threads = _local.search_threads(
            query,
            limit=limit,
            unread=unread,
            include_participants=include_participants,
            account=account,
        )
        warnings: list[str] = []
        if not threads:
            warnings.append("No threads matched — try broader search terms")
        return ok("thread.search", {
            "query": query,
            "source": "local-db",
            "limit": limit,
            "returned": len(threads),
            "threads": threads,
        }, warnings=warnings)
    except Exception as e:
        return fail("thread.search", [classify_exception(e)])
