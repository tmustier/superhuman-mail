"""Comment operations — post, read, discard."""
from __future__ import annotations

import html as html_mod
import json
import random
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from . import _auth, _config
from ._envelope import classify_exception, fail, ok

# ---------------------------------------------------------------------------
# ID generation (Superhuman ExternalID format)
# ---------------------------------------------------------------------------

BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _base62_encode(num: int, pad: int = 0) -> str:
    if num == 0:
        return BASE62[0] * max(pad, 1)
    result = []
    while num > 0:
        result.append(BASE62[num % 62])
        num //= 62
    s = "".join(reversed(result))
    return s.zfill(pad) if pad else s


def _comment_id() -> str:
    ts_encoded = _base62_encode(int(time.time()), 6)
    entropy = "".join(random.choice(BASE62) for _ in range(7))
    shard = _config.api("team_shard_key")
    return f"cmt_1{ts_encoded}{shard}{entropy}"


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _build_html(text: str, mentions: list[dict[str, str]] | None = None) -> str:
    escaped = html_mod.escape(text)
    if mentions:
        sorted_mentions = sorted(mentions, key=lambda m: len(m.get("fullName", m.get("email", ""))), reverse=True)
        for m in sorted_mentions:
            name = m.get("fullName", m.get("email", ""))
            email = m.get("email", "")
            safe_email = html_mod.escape(email)
            safe_name = html_mod.escape(name)
            tag = f'<a data-mention="{safe_email}" data-name="{safe_name}">@{safe_name}</a>\u200b'
            if name:
                escaped = escaped.replace(f"@{html_mod.escape(name)}", tag)
            if email and email != name:
                escaped = escaped.replace(f"@{html_mod.escape(email)}", tag)

    paragraphs = escaped.split("\n\n")
    body = "".join(f"<p>{p}</p>" for p in paragraphs if p.strip())
    return f"<div>{body}</div>" if body else "<div><p></p></div>"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def post(
    thread_id: str,
    body: str,
    mentions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Post a comment on a thread."""
    cid = _comment_id()
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        payload = {
            "threadId": thread_id,
            "comment": {
                "id": cid,
                "body": _build_html(body, mentions),
                "clientCreatedAt": now,
                "contentType": "text/superhuman-comment-v1",
            },
            "authorName": _config.api("author_name"),
            "mentions": mentions or [],
            "metricsMetadata": {"commentBodyLength": len(body), "isSayHiNudge": False},
        }
        req = urllib.request.Request(
            "https://mail.superhuman.com/~backend/v3/comments.write",
            data=json.dumps(payload).encode(),
            headers=_auth.api_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        return ok("comment.post", {
            "thread_id": thread_id,
            "comment_id": cid,
            "container_id": result.get("containerId", ""),
        })
    except Exception as e:
        return fail("comment.post", [classify_exception(e)])


def read(thread_id: str) -> dict[str, Any]:
    """Read all comments on a thread."""
    try:
        google_id = _config.api("google_id")
        payload = {
            "reads": [{"path": f"users/{google_id}/threads/{thread_id}"}],
            "pageSize": 100,
        }
        req = urllib.request.Request(
            "https://mail.superhuman.com/~backend/v3/userdata.read",
            data=json.dumps(payload).encode(),
            headers=_auth.api_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        results = data.get("results", [])
        thread_data = results[0].get("value", {}) if results else {}
        teams = thread_data.get("teams", {})

        comments: list[dict[str, Any]] = []
        for _team_id, team in teams.items():
            for _cont_id, container in team.get("containers", {}).items():
                for msg_id, msg in container.get("messages", {}).items():
                    comment_data = msg.get("comment", {})
                    sharing = msg.get("sharing", {})
                    body_html = comment_data.get("body", "")
                    # Convert HTML to plain text
                    text = re.sub(r"</p>\s*<p[^>]*>", "\n\n", body_html)
                    text = re.sub(r"<br\s*/?>", "\n", text)
                    text = re.sub(r"<[^>]+>", "", text).strip()
                    text = text.replace("\u200b", "")
                    text = html_mod.unescape(text)

                    comments.append({
                        "id": comment_data.get("id", msg_id),
                        "text": text,
                        "html": body_html,
                        "author": sharing.get("name", ""),
                        "author_email": sharing.get("by", ""),
                        "created_at": comment_data.get("createdAt", comment_data.get("clientCreatedAt", "")),
                        "mentions": msg.get("mentions", []),
                    })

        comments.sort(key=lambda c: c.get("created_at", ""))
        return ok("comment.read", {"thread_id": thread_id, "comment_count": len(comments), "comments": comments})
    except Exception as e:
        return fail("comment.read", [classify_exception(e)])


def discard(thread_id: str, comment_id: str) -> dict[str, Any]:
    """Discard (delete) a comment from a thread."""
    try:
        req = urllib.request.Request(
            "https://mail.superhuman.com/~backend/v3/comments.discard",
            data=json.dumps({"threadId": thread_id, "commentId": comment_id}).encode(),
            headers=_auth.api_headers(),
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15)
        return ok("comment.discard", {"thread_id": thread_id, "comment_id": comment_id})
    except Exception as e:
        return fail("comment.discard", [classify_exception(e)])
