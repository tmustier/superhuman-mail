"""Local Superhuman SQLite DB access.

Reads thread and message data from Superhuman's local cache.
The Superhuman Electron app stores data in an IndexedDB-backed
SQLite database under ~/Library/Application Support/Superhuman/.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from . import _config

# ---------------------------------------------------------------------------
# FTS delimiters used by Superhuman to separate messages in search content
# ---------------------------------------------------------------------------

_FTS_DELIMITER_RE = re.compile(r"[\uf8f5\uf8f8]")
_QUOTED_HISTORY_RE = re.compile(
    r"\b(?:"
    r"On\s+[A-Z][a-z]{2},?.+?wrote:|"
    r"From:\s+.+?\s+Sent:\s+.+?\s+To:\s+"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------


def get_db_path(account: str | None = None) -> Path:
    """Get path to a readable copy of the Superhuman SQLite DB."""
    target = account or _config.email_account()
    match = next((a for a in _config.accounts() if a.get("email") == target), None)
    if not match or not match.get("db_file"):
        raise RuntimeError(f"No Superhuman account config for {target}")

    data_file = _config.superhuman_base() / "File System/000/t/00" / str(match["db_file"])
    if not data_file.exists():
        raise RuntimeError(f"Superhuman DB not found: {data_file}")

    safe = target.replace("@", "_").replace(".", "_")
    temp_db = Path(f"/tmp/superhuman_{safe}.sqlite3")
    if temp_db.exists() and data_file.stat().st_mtime <= temp_db.stat().st_mtime:
        return temp_db

    with open(data_file, "rb") as src:
        src.seek(4096)
        content = src.read()
    with open(temp_db, "wb") as dst:
        dst.write(content)
    return temp_db


def _connection(account: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(account))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# FTS body extraction
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _anchors(text: str) -> list[str]:
    words = _normalize(text).split()
    out: list[str] = []
    for size in (15, 8, 5):
        if len(words) >= size:
            out.append(" ".join(words[:size]))
    if words:
        out.append(" ".join(words))
    return list(dict.fromkeys(a for a in out if a))


def _split_segments(content: str) -> list[str]:
    return [s for s in (_normalize(p) for p in _FTS_DELIMITER_RE.split(content)) if s]


def _matching_indexes(segments: list[str], snippet: str | None) -> list[int]:
    if not snippet:
        return []
    for anchor in _anchors(snippet):
        hits = [i for i, seg in enumerate(segments) if anchor in seg]
        if hits:
            return hits
    return []


def _extract_segment(content: str, snippet: str, prev: str | None, nxt: str | None) -> str | None:
    segments = _split_segments(content)
    if not segments:
        return None
    candidates = _matching_indexes(segments, snippet)
    if not candidates:
        return None

    lo = max(_matching_indexes(segments, prev)) if _matching_indexes(segments, prev) else -1
    hi = min(_matching_indexes(segments, nxt)) if _matching_indexes(segments, nxt) else len(segments)

    windowed = [i for i in candidates if lo < i < hi]
    if windowed:
        candidates = windowed

    anchors = _anchors(snippet)
    if anchors:
        starts = [i for i in candidates if segments[i].startswith(anchors[0])]
        if starts:
            candidates = starts

    slen = len(_normalize(snippet))
    return segments[min(candidates, key=lambda i: (abs(len(segments[i]) - slen), i))]


def _truncate_quoted(text: str) -> str:
    for candidate in [text, _normalize(text)]:
        m = _QUOTED_HISTORY_RE.search(candidate)
        if m:
            return candidate[: m.start()].strip()
    return text


def _clean(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _FTS_DELIMITER_RE.sub(" ", cleaned)
    cleaned = _truncate_quoted(cleaned)
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in cleaned.split("\n")]
    out: list[str] = []
    prev_blank = False
    for line in lines:
        blank = line == ""
        if blank and prev_blank:
            continue
        out.append(line)
        prev_blank = blank
    return "\n".join(out).strip()


def _choose_body(current: str, extracted: str, snippet: str) -> str:
    c, e, s = _clean(current), _clean(extracted), _clean(snippet)
    if not e:
        return c or s
    if not c:
        return e
    if e == s and c != s:
        return c
    if _QUOTED_HISTORY_RE.search(_normalize(current)) and e:
        return e
    if len(c) <= len(s) + 10 and len(e) > len(c):
        return e
    if len(e) + 20 < len(c):
        return e
    if len(e) > len(c) * 1.5:
        return e
    return c


# ---------------------------------------------------------------------------
# Thread reading
# ---------------------------------------------------------------------------


def _get_fts_content(conn: sqlite3.Connection, thread_id: str) -> str:
    try:
        row = conn.execute(
            "SELECT c2content FROM thread_search_content WHERE c0thread_id = ?",
            (thread_id,),
        ).fetchone()
        return str(row[0]) if row and row[0] else ""
    except Exception:
        return ""


def get_thread_json(thread_id: str, account: str | None = None) -> dict[str, Any]:
    """Read raw thread JSON from local SQLite DB."""
    conn = _connection(account)
    try:
        row = conn.execute("SELECT json FROM threads WHERE thread_id = ?", (thread_id,)).fetchone()
        if not row:
            raise RuntimeError(f"Thread not found in local DB: {thread_id}")
        return json.loads(row[0]) if row[0] else {}
    finally:
        conn.close()


def get_messages(thread_id: str, account: str | None = None) -> list[dict[str, Any]]:
    """Read and normalize thread messages from local DB with FTS body extraction."""
    target = account or _config.email_account()
    conn = _connection(target)
    try:
        row = conn.execute("SELECT json FROM threads WHERE thread_id = ?", (thread_id,)).fetchone()
        if not row:
            return []

        raw = json.loads(row[0]) if row[0] else {}
        raw_messages = raw.get("messages", [])
        messages: list[dict[str, Any]] = []

        for msg in raw_messages:
            from_info = msg.get("from", {}) or {}
            raw_body = msg.get("body", {})
            if isinstance(raw_body, dict):
                body = {"text": str(raw_body.get("text", "")), "html": str(raw_body.get("html", ""))}
            elif isinstance(raw_body, str):
                body = {"text": raw_body, "html": ""}
            else:
                body = {"text": "", "html": ""}

            snippet = str(msg.get("snippet", ""))
            if not body["text"] and not body["html"] and snippet:
                body["text"] = snippet

            date_raw = msg.get("date")
            if isinstance(date_raw, (int, float)):
                date_str = datetime.fromtimestamp(date_raw / 1000).isoformat()
            elif date_raw is None:
                date_str = None
            else:
                date_str = str(date_raw)

            messages.append({
                "id": str(msg.get("id", "")),
                "sender": {"email": str(from_info.get("email", "")), "name": str(from_info.get("name", ""))},
                "to": list(msg.get("to", []) or []),
                "cc": list(msg.get("cc", []) or []),
                "date": date_str,
                "subject": str(msg.get("subject", "")),
                "body": body,
                "snippet": snippet,
                "attachments": [
                    {"name": str(a.get("name", "")), "type": str(a.get("type", "")), "size": int(a.get("size", 0) or 0)}
                    for a in (msg.get("attachments", []) or [])
                ],
            })

        # Enrich body text from FTS content
        if messages:
            fts = _get_fts_content(conn, thread_id)
            for i, msg in enumerate(messages):
                snip = msg.pop("snippet", "")
                text = msg["body"]["text"]
                if fts and snip:
                    prev_snip = messages[i - 1].get("_snip") if i > 0 else None
                    nxt_snip = messages[i + 1].get("_snip") if i + 1 < len(messages) else None
                    msg["_snip"] = snip
                    extracted = _extract_segment(fts, snip, prev_snip, nxt_snip) or ""
                    msg["body"]["text"] = _choose_body(text, extracted, snip)
                else:
                    msg["body"]["text"] = _clean(text)

            for msg in messages:
                msg.pop("_snip", None)

        return messages
    finally:
        conn.close()
