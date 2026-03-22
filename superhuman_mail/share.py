"""Draft sharing — share and unshare drafts with team."""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from . import _auth, _config
from ._envelope import classify_exception, fail, ok


def share(
    thread_id: str,
    draft_id: str,
    *,
    name: str | None = None,
    add: list[str] | None = None,
) -> dict[str, Any]:
    """Share an existing draft, generating a collaboration link."""
    try:
        path = f"users/{_config.api('google_id')}/threads/{thread_id}/messages/{draft_id}"
        payload: dict[str, Any] = {
            "path": path,
            "name": name or _config.api("author_name"),
            "add": list(add or []),
        }
        req = urllib.request.Request(
            "https://mail.superhuman.com/~backend/v3/drafts.share",
            data=json.dumps(payload).encode(),
            headers=_auth.api_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())

        return ok("share", {
            "thread_id": thread_id,
            "draft_id": draft_id,
            "link": result.get("link", ""),
            "container_id": result.get("containerId", ""),
        })
    except Exception as e:
        return fail("share", [classify_exception(e)])


def unshare(thread_id: str, draft_id: str) -> dict[str, Any]:
    """Unshare a previously shared draft."""
    try:
        payload = {
            "path": f"users/{_config.api('google_id')}/threads/{thread_id}/messages/{draft_id}",
        }
        req = urllib.request.Request(
            "https://mail.superhuman.com/~backend/v3/drafts.unshare",
            data=json.dumps(payload).encode(),
            headers=_auth.api_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            result = json.loads(raw) if raw else {}

        return ok("unshare", {
            "thread_id": thread_id,
            "draft_id": draft_id,
        })
    except Exception as e:
        return fail("unshare", [classify_exception(e)])
