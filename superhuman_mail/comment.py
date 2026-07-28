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
from ._envelope import classify_exception, error, fail, ok

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
    escaped = html_mod.escape(text.replace("\r\n", "\n").replace("\r", "\n"))
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

    # comments.write currently rejects <br> tags with HTTP 400. Render each
    # non-empty source line as a paragraph instead: this preserves readable
    # line separation while staying inside the endpoint's accepted HTML subset.
    # Do not revert to literal newlines inside one <p>; HTML collapses them.
    lines = (line for line in escaped.strip("\n").splitlines() if line.strip())
    body = "".join(f"<p>{line}</p>" for line in lines)
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


def _comments_from_thread_data(thread_data: dict[str, Any]) -> list[dict[str, Any]]:
    teams = thread_data.get("teams", {})
    comments: list[dict[str, Any]] = []
    for _team_id, team in teams.items():
        for _cont_id, container in team.get("containers", {}).items():
            for msg_id, msg in container.get("messages", {}).items():
                comment_data = msg.get("comment", {})
                sharing = msg.get("sharing", {})
                body_html = comment_data.get("body", "")
                # Convert HTML to plain text.
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
    return comments


def read_many(thread_ids: list[str], *, batch_size: int = 2) -> dict[str, Any]:
    """Read comments for many threads using batched userdata requests.

    A partial read is a failed command, not a successful result with missing
    threads. Callers that advance checkpoints can therefore fail closed.
    """
    unique_thread_ids = list(dict.fromkeys(str(value).strip() for value in thread_ids if str(value).strip()))
    if not unique_thread_ids:
        return fail("comment.read-many", [error("input", "THREAD_IDS_REQUIRED", False, "Provide at least one thread ID")])
    if batch_size < 1 or batch_size > 2:
        return fail("comment.read-many", [error("input", "INVALID_BATCH_SIZE", False, "--batch-size must be 1 or 2")])

    comments_by_thread: dict[str, list[dict[str, Any]]] = {}
    failures: list[dict[str, Any]] = []
    try:
        google_id = _config.api("google_id")
        headers = _auth.api_headers()
        for offset in range(0, len(unique_thread_ids), batch_size):
            batch = unique_thread_ids[offset : offset + batch_size]
            payload = {
                "reads": [{"path": f"users/{google_id}/threads/{thread_id}"} for thread_id in batch],
                "pageSize": 100,
            }
            req = urllib.request.Request(
                "https://mail.superhuman.com/~backend/v3/userdata.read",
                data=json.dumps(payload).encode(),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
            except Exception as exc:
                classified = classify_exception(exc)
                for thread_id in batch:
                    failures.append({"thread_id": thread_id, "error": classified})
                continue

            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                invalid = error("network", "INVALID_USERDATA_RESPONSE", True, "Superhuman returned no results list")
                for thread_id in batch:
                    failures.append({"thread_id": thread_id, "error": invalid})
                continue
            results_by_thread: dict[str, dict[str, Any]] = {}
            for index, result in enumerate(results):
                if not isinstance(result, dict):
                    continue
                path_thread_id = str(result.get("path") or "").rstrip("/").split("/")[-1]
                if not path_thread_id and index < len(batch):
                    path_thread_id = batch[index]
                if path_thread_id:
                    results_by_thread[path_thread_id] = result
            for thread_id in batch:
                result = results_by_thread.get(thread_id)
                # userdata.read omits paths that have no stored userdata. That is
                # a successful empty read, matching the single-thread command's
                # long-standing behaviour.
                if result is None:
                    comments_by_thread[thread_id] = []
                    continue
                value = result.get("value")
                if value is None:
                    comments_by_thread[thread_id] = []
                    continue
                if not isinstance(value, dict):
                    failures.append({
                        "thread_id": thread_id,
                        "error": error("network", "INVALID_USERDATA_RESULT", True, "Superhuman returned an invalid thread result"),
                    })
                    continue
                comments_by_thread[thread_id] = _comments_from_thread_data(value)
    except Exception as exc:
        result = fail("comment.read-many", [classify_exception(exc)])
        result["data"] = {
            "requested_count": len(unique_thread_ids),
            "succeeded_count": len(comments_by_thread),
            "failed_count": len(unique_thread_ids) - len(comments_by_thread),
            "complete": False,
            "comments_by_thread": comments_by_thread,
            "failures": failures,
        }
        return result

    data = {
        "requested_count": len(unique_thread_ids),
        "succeeded_count": len(comments_by_thread),
        "failed_count": len(failures),
        "complete": not failures and len(comments_by_thread) == len(unique_thread_ids),
        "comments_by_thread": comments_by_thread,
        "failures": failures,
    }
    if failures:
        result = fail(
            "comment.read-many",
            [error("network", "PARTIAL_COMMENT_READ", True, f"Failed to read {len(failures)} of {len(unique_thread_ids)} threads")],
        )
        result["data"] = data
        return result
    return ok("comment.read-many", data)


def read(thread_id: str) -> dict[str, Any]:
    """Read all comments on a thread."""
    result = read_many([thread_id], batch_size=1)
    if result.get("status") != "succeeded":
        result["command"] = "comment.read"
        return result
    comments = result["data"]["comments_by_thread"][thread_id]
    return ok("comment.read", {"thread_id": thread_id, "comment_count": len(comments), "comments": comments})


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
