"""Bounded, privacy-aware reads from Superhuman's local cache.

This module is deliberately separate from the legacy local reader.  It never
creates a reusable database copy: a wrapped cache is copied to an anonymous
0600 file, opened immutable/read-only through its descriptor, and never linked.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal

from . import _config
from ._envelope import error, fail, ok

COMMAND = "reader.scan"
CONTRACT_VERSION = "1.0"
WRAPPER_BYTES = 4096
COPY_CHUNK_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_THREADS_PER_ACCOUNT = 500
MAX_MESSAGES_INSPECTED_PER_ACCOUNT = 10_000
MAX_MESSAGES_PER_ACCOUNT = 2_000
MAX_RECORDS = 1_000
MAX_SELECTOR_VALUES = 100
MAX_PARTICIPANTS_PER_MESSAGE = 100
MAX_PARTICIPANTS = 5_000
MAX_ATTACHMENTS_PER_MESSAGE = 50
MAX_ATTACHMENTS = 2_000
MAX_ROW_JSON_BYTES = 8 * 1024 * 1024
MAX_ID_CHARS = 512
MAX_EMAIL_CHARS = 320
MAX_LABEL_CHARS = 512
MAX_SUBJECT_CHARS = 2_000
MAX_BODY_FIELD_CHARS = 64_000
MAX_NAME_CHARS = 1_000
MAX_FILENAME_CHARS = 2_000
MAX_CONTENT_CHARS = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024

_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+$")


@dataclass(frozen=True)
class ScanRequest:
    since: datetime
    before: datetime
    accounts: tuple[str, ...]
    projection: Literal["metadata", "full"]
    threads: tuple[str, ...]
    people: tuple[str, ...]


class ReaderError(Exception):
    """A stable, privacy-safe reader failure."""

    def __init__(
        self, code: str, hint: str, *, cls: str = "input", retryable: bool = False
    ) -> None:
        super().__init__(code)
        self.code = code
        self.hint = hint
        self.cls = cls
        self.retryable = retryable


def _epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _utc_z(value: int) -> str:
    dt = datetime.fromtimestamp(value / 1000, tz=UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _cached_date_epoch_ms(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and _TIME_RE.fullmatch(value):
        try:
            parsed = datetime.fromisoformat(value)
            epoch_ms = _epoch_ms(parsed)
        except (OverflowError, ValueError) as exc:
            raise ReaderError(
                "MALFORMED_CACHE_JSON",
                "A cached message date is outside the supported range",
                cls="conflict",
            ) from exc
        if epoch_ms >= 0:
            return epoch_ms
    raise ReaderError(
        "MALFORMED_CACHE_JSON",
        "A cached message date is not a supported UTC timestamp",
        cls="conflict",
    )


def _parse_time(raw: str, option: str) -> datetime:
    if not _TIME_RE.fullmatch(raw):
        raise ReaderError(
            "INVALID_TIME",
            f"{option} must be an exact UTC-Z timestamp (YYYY-MM-DDTHH:MM:SS[.sss]Z)",
        )
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ReaderError(
            "INVALID_TIME", f"{option} must be a valid UTC-Z timestamp"
        ) from exc
    return parsed


def _unique(
    values: list[str], option: str, *, maximum: int = MAX_SELECTOR_VALUES
) -> tuple[str, ...]:
    if len(values) > maximum:
        raise ReaderError(
            "SELECTOR_LIMIT_EXCEEDED", f"{option} accepts at most {maximum} values"
        )
    if any(not isinstance(item, str) or not item for item in values):
        raise ReaderError(
            "INVALID_SELECTOR", f"{option} values must be non-empty strings"
        )
    if len(values) != len(set(values)):
        raise ReaderError(
            "DUPLICATE_SELECTOR", f"{option} values must not be duplicated"
        )
    return tuple(values)


def _normal_email(raw: str, option: str) -> str:
    value = raw.strip().casefold()
    if len(value) > MAX_EMAIL_CHARS or not _EMAIL_RE.fullmatch(value):
        raise ReaderError(
            "INVALID_SELECTOR", f"{option} values must be exact email addresses"
        )
    return value


def _configured_accounts() -> dict[str, dict[str, Any]]:
    try:
        raw_accounts = _config.accounts()
    except Exception as exc:
        raise ReaderError(
            "CONFIG_UNAVAILABLE", "Superhuman account configuration is unavailable"
        ) from exc
    result: dict[str, dict[str, Any]] = {}
    for raw in raw_accounts:
        if not isinstance(raw, dict) or not isinstance(raw.get("email"), str):
            raise ReaderError(
                "CONFIG_INVALID", "Superhuman account configuration is invalid"
            )
        email = raw["email"]
        if (
            not email
            or email.strip() != email
            or len(email) > MAX_EMAIL_CHARS
            or not _EMAIL_RE.fullmatch(email.casefold())
            or email in result
        ):
            raise ReaderError(
                "CONFIG_INVALID",
                "Superhuman account configuration contains invalid or duplicate accounts",
            )
        result[email] = raw
    if not result:
        raise ReaderError("CONFIG_INVALID", "No Superhuman accounts are configured")
    if len(result) > MAX_SELECTOR_VALUES:
        raise ReaderError(
            "CONFIG_INVALID",
            f"At most {MAX_SELECTOR_VALUES} Superhuman accounts may be scanned",
        )
    return result


def validate_request(
    *,
    since: str,
    before: str,
    accounts: list[str],
    projection: str,
    threads: list[str],
    people: list[str],
) -> ScanRequest:
    """Validate CLI values without touching a mail database."""
    start = _parse_time(since, "--since")
    end = _parse_time(before, "--before")
    if start >= end:
        raise ReaderError("INVALID_RANGE", "--since must be earlier than --before")
    if projection not in ("metadata", "full"):
        raise ReaderError("INVALID_PROJECTION", "--projection must be metadata or full")

    configured = _configured_accounts()
    selected = _unique(accounts, "--account") if accounts else tuple(sorted(configured))
    unknown = [account for account in selected if account not in configured]
    if unknown:
        raise ReaderError(
            "UNKNOWN_ACCOUNT", "Every --account must exactly match a configured account"
        )

    selected_threads = _unique(threads, "--thread")
    for thread_id in selected_threads:
        if (
            len(thread_id) > MAX_ID_CHARS
            or thread_id.strip() != thread_id
            or any(ch.isspace() for ch in thread_id)
        ):
            raise ReaderError(
                "INVALID_SELECTOR",
                "--thread values must be exact non-whitespace identifiers",
            )

    raw_people = _unique(people, "--person")
    normalized_people = tuple(_normal_email(value, "--person") for value in raw_people)
    if len(normalized_people) != len(set(normalized_people)):
        raise ReaderError(
            "DUPLICATE_SELECTOR",
            "--person values must not duplicate after email normalization",
        )

    return ScanRequest(
        start, end, selected, projection, selected_threads, normalized_people
    )


def _source_path(account: dict[str, Any]) -> Path:
    db_file = account.get("db_file")
    if not isinstance(db_file, str) or not db_file or Path(db_file).name != db_file:
        raise ReaderError(
            "CONFIG_INVALID", "The selected account cache configuration is invalid"
        )
    try:
        return _config.superhuman_base() / "File System/000/t/00" / db_file
    except Exception as exc:
        raise ReaderError(
            "CONFIG_UNAVAILABLE", "Superhuman cache configuration is unavailable"
        ) from exc


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


@contextlib.contextmanager
def _anonymous_snapshot() -> Iterator[BinaryIO]:
    with tempfile.TemporaryFile(mode="w+b") as snapshot:
        snapshot_stat = os.fstat(snapshot.fileno())
        if (
            not stat.S_ISREG(snapshot_stat.st_mode)
            or snapshot_stat.st_nlink != 0
            or snapshot_stat.st_mode & 0o777 != 0o600
        ):
            raise ReaderError(
                "SNAPSHOT_CREATE_FAILED",
                "An anonymous 0600 cache snapshot could not be created",
                cls="conflict",
            )
        yield snapshot


@contextlib.contextmanager
def transient_connection(source: Path) -> Iterator[tuple[sqlite3.Connection, int]]:
    """Yield an immutable/query-only connection to an anonymous snapshot."""
    src_fd = -1
    resources = contextlib.ExitStack()
    snapshot: BinaryIO | None = None
    conn: sqlite3.Connection | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            src_fd = os.open(source, flags)
            before_fd = os.fstat(src_fd)
            before_path = os.stat(source, follow_symlinks=False)
        except OSError as exc:
            raise ReaderError(
                "CACHE_UNAVAILABLE",
                "The selected account cache could not be opened",
                cls="not-found",
            ) from exc
        if not stat.S_ISREG(before_fd.st_mode) or _identity(before_fd) != _identity(
            before_path
        ):
            raise ReaderError(
                "CACHE_UNSAFE",
                "The selected account cache is not a stable regular file",
                cls="conflict",
            )
        if before_fd.st_size <= WRAPPER_BYTES or before_fd.st_size > MAX_SOURCE_BYTES:
            raise ReaderError(
                "CACHE_SIZE_INVALID",
                "The selected account cache has an unsupported size",
                cls="conflict",
            )

        snapshot = resources.enter_context(_anonymous_snapshot())
        os.lseek(src_fd, WRAPPER_BYTES, os.SEEK_SET)
        copied = 0
        while True:
            block = os.read(src_fd, COPY_CHUNK_BYTES)
            if not block:
                break
            view = memoryview(block)
            while view:
                written = os.write(snapshot.fileno(), view)
                if written <= 0:
                    raise ReaderError(
                        "SNAPSHOT_WRITE_FAILED",
                        "The transient cache snapshot could not be written",
                        cls="conflict",
                    )
                view = view[written:]
            copied += len(block)
        os.fsync(snapshot.fileno())

        after_fd = os.fstat(src_fd)
        try:
            after_path = os.stat(source, follow_symlinks=False)
        except OSError as exc:
            raise ReaderError(
                "SOURCE_CHANGED",
                "The selected account cache changed during snapshot",
                cls="conflict",
                retryable=True,
            ) from exc
        if _identity(before_fd) != _identity(after_fd) or _identity(
            before_fd
        ) != _identity(after_path):
            raise ReaderError(
                "SOURCE_CHANGED",
                "The selected account cache changed during snapshot",
                cls="conflict",
                retryable=True,
            )
        os.close(src_fd)
        src_fd = -1

        uri = f"file:/dev/fd/{snapshot.fileno()}?mode=ro&immutable=1"
        try:
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            raise ReaderError(
                "MALFORMED_CACHE",
                "The selected account cache is not a readable SQLite database",
                cls="conflict",
            ) from exc
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("PRAGMA schema_version").fetchone()
        except sqlite3.Error as exc:
            raise ReaderError(
                "MALFORMED_CACHE",
                "The selected account cache is not a readable SQLite database",
                cls="conflict",
            ) from exc
        yield conn, copied
    finally:
        if conn is not None:
            conn.close()
        if src_fd >= 0:
            os.close(src_fd)
        resources.close()


def _valid_text(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _strict_string(value: Any, field: str, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or not _valid_text(value)
    ):
        raise ReaderError(
            "MALFORMED_CACHE_JSON",
            f"A cached {field} value is missing or invalid",
            cls="conflict",
        )
    return value


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _valid_text(value):
        raise ReaderError(
            "MALFORMED_CACHE_JSON", f"A cached {field} value is invalid", cls="conflict"
        )
    return value


def _content(value: str | None, maximum: int, provenance: str) -> dict[str, Any]:
    if value is None or value == "":
        return {"value": None, "coverage": "unavailable", "provenance": "none"}
    if len(value) > maximum:
        return {
            "value": value[:maximum],
            "coverage": "truncated",
            "provenance": provenance,
        }
    return {"value": value, "coverage": "complete", "provenance": provenance}


def _address(raw: Any, projection: str, field: str) -> tuple[dict[str, Any], str]:
    if not isinstance(raw, dict):
        raise ReaderError(
            "MALFORMED_CACHE_JSON",
            f"A cached {field} address is invalid",
            cls="conflict",
        )
    email = raw.get("email")
    if not isinstance(email, str):
        raise ReaderError(
            "MALFORMED_CACHE_JSON",
            f"A cached {field} address is invalid",
            cls="conflict",
        )
    normalized = email.strip().casefold()
    if (
        len(normalized) > MAX_EMAIL_CHARS
        or not _valid_text(normalized)
        or not _EMAIL_RE.fullmatch(normalized)
    ):
        raise ReaderError(
            "MALFORMED_CACHE_JSON", f"A cached {field} email is invalid", cls="conflict"
        )
    result: dict[str, Any] = {"email": normalized}
    if projection == "full":
        if raw.get("name") is not None:
            name = raw.get("name")
            provenance = f"message.{field}.name"
        else:
            name = raw.get("raw")
            provenance = f"message.{field}.raw"
        result["display_name"] = _content(
            _optional_string(name, "display name"), MAX_NAME_CHARS, provenance
        )
    return result, normalized


def _addresses(
    raw: Any, projection: str, field: str, *, singular: bool = False
) -> tuple[list[dict[str, Any]], list[str]]:
    if raw is None:
        values: list[Any] = []
    elif singular and isinstance(raw, dict):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        raise ReaderError(
            "MALFORMED_CACHE_JSON",
            f"Cached {field} addresses are invalid",
            cls="conflict",
        )
    parsed = [_address(value, projection, field) for value in values]
    return [item[0] for item in parsed], [item[1] for item in parsed]


def _attachments(raw: Any, projection: str) -> tuple[list[dict[str, Any]], bool]:
    if raw is None:
        values: list[Any] = []
    elif isinstance(raw, list):
        values = raw
    else:
        raise ReaderError(
            "MALFORMED_CACHE_JSON", "Cached attachments are invalid", cls="conflict"
        )
    parsed: list[dict[str, Any]] = []
    for attachment in values:
        if not isinstance(attachment, dict):
            raise ReaderError(
                "MALFORMED_CACHE_JSON", "A cached attachment is invalid", cls="conflict"
            )
        size = attachment.get("size", 0)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ReaderError(
                "MALFORMED_CACHE_JSON",
                "A cached attachment size is invalid",
                cls="conflict",
            )
        media_type = _optional_string(
            attachment.get("type", attachment.get("contentType")),
            "attachment media type",
        )
        if media_type is not None and len(media_type) > MAX_LABEL_CHARS:
            raise ReaderError(
                "MALFORMED_CACHE_JSON",
                "A cached attachment media type is oversized",
                cls="conflict",
            )
        item: dict[str, Any] = {"size_bytes": size, "media_type": media_type}
        attachment_ids = [
            _strict_string(value, "attachment id", maximum=MAX_ID_CHARS)
            for value in (attachment.get("id"), attachment.get("attachmentId"))
            if value is not None
        ]
        if len(set(attachment_ids)) > 1:
            raise ReaderError(
                "MALFORMED_CACHE_JSON",
                "A cached attachment has conflicting identifiers",
                cls="conflict",
            )
        if attachment_ids:
            item["attachment_id"] = attachment_ids[0]
        if projection == "full":
            if "name" in attachment:
                filename = attachment.get("name")
                provenance = "message.attachments.name"
            else:
                filename = attachment.get("filename")
                provenance = "message.attachments.filename"
            item["filename"] = _content(
                _optional_string(filename, "attachment filename"),
                MAX_FILENAME_CHARS,
                provenance,
            )
        parsed.append(item)
    return parsed[:MAX_ATTACHMENTS_PER_MESSAGE], len(
        parsed
    ) > MAX_ATTACHMENTS_PER_MESSAGE


def _message(
    raw: Any, account: str, thread_id: str, projection: str
) -> tuple[dict[str, Any], set[str], bool, bool, bool]:
    if not isinstance(raw, dict):
        raise ReaderError(
            "MALFORMED_CACHE_JSON", "A cached message is not an object", cls="conflict"
        )
    message_id = _strict_string(raw.get("id"), "message id", maximum=MAX_ID_CHARS)
    date = _cached_date_epoch_ms(raw.get("date"))
    try:
        utc_date = _utc_z(date)
    except (OverflowError, OSError, ValueError) as exc:
        raise ReaderError(
            "MALFORMED_CACHE_JSON",
            "A cached message date is outside the supported range",
            cls="conflict",
        ) from exc

    from_values, from_emails = _addresses(
        raw.get("from"), projection, "from", singular=True
    )
    to_values, to_emails = _addresses(raw.get("to"), projection, "to")
    cc_values, cc_emails = _addresses(raw.get("cc"), projection, "cc")
    bcc_values, bcc_emails = _addresses(raw.get("bcc"), projection, "bcc")
    address_groups = [from_values, to_values, cc_values, bcc_values]
    participants_remaining = MAX_PARTICIPANTS_PER_MESSAGE
    kept_address_groups: list[list[dict[str, Any]]] = []
    for values in address_groups:
        kept_address_groups.append(values[:participants_remaining])
        participants_remaining = max(0, participants_remaining - len(values))
    participant_truncated = sum(len(values) for values in address_groups) > sum(
        len(values) for values in kept_address_groups
    )
    from_values, to_values, cc_values, bcc_values = kept_address_groups
    attachments, attachments_truncated = _attachments(
        raw.get("attachments"), projection
    )

    labels_raw = raw.get("labelIds", [])
    if not isinstance(labels_raw, list):
        raise ReaderError(
            "MALFORMED_CACHE_JSON", "Cached message labels are invalid", cls="conflict"
        )
    labels: list[str] = []
    for label in labels_raw:
        labels.append(_strict_string(label, "label", maximum=MAX_LABEL_CHARS))
    if len(labels) != len(set(labels)):
        raise ReaderError(
            "MALFORMED_CACHE_JSON",
            "Cached message labels contain duplicates",
            cls="conflict",
        )

    draft_sources: list[str] = []
    if raw.get("isDraft") is True:
        draft_sources.append("message.isDraft")
    elif raw.get("isDraft") not in (None, False):
        raise ReaderError(
            "MALFORMED_CACHE_JSON", "Cached draft evidence is invalid", cls="conflict"
        )
    for draft_field in ("draftId", "superhumanDraftId", "superhumanOwnDraftId"):
        draft_value = raw.get(draft_field)
        if isinstance(draft_value, str) and draft_value:
            draft_sources.append(f"message.{draft_field}")
        elif draft_value is not None:
            raise ReaderError(
                "MALFORMED_CACHE_JSON",
                "Cached draft evidence is invalid",
                cls="conflict",
            )
    if "DRAFT" in labels:
        draft_sources.append("message.labelIds:DRAFT")

    item: dict[str, Any] = {
        "account_id": account,
        "thread_id": thread_id,
        "message_id": message_id,
        "date": utc_date,
        "date_epoch_ms": date,
        "addresses": {
            "from": from_values,
            "to": to_values,
            "cc": cc_values,
            "bcc": bcc_values,
        },
        "addresses_coverage": "truncated" if participant_truncated else "complete",
        "labels": labels,
        "draft": {"is_draft": bool(draft_sources), "evidence": draft_sources},
        "read": {"is_unread": "UNREAD" in labels, "evidence": "message.labelIds"},
        "attachments": attachments,
        "attachments_coverage": "truncated" if attachments_truncated else "complete",
    }
    if projection == "full":
        subject = _optional_string(raw.get("subject"), "subject")
        body_raw = raw.get("body")
        text: str | None = None
        html: str | None = None
        text_provenance = "message.body.text"
        if body_raw is None:
            pass
        elif isinstance(body_raw, str):
            text = body_raw
            text_provenance = "message.body"
        elif isinstance(body_raw, dict):
            text = _optional_string(body_raw.get("text"), "body text")
            html = _optional_string(body_raw.get("html"), "body HTML")
        else:
            raise ReaderError(
                "MALFORMED_CACHE_JSON",
                "A cached message body is invalid",
                cls="conflict",
            )
        item["content"] = {
            "subject": _content(subject, MAX_SUBJECT_CHARS, "message.subject"),
            "body": {
                "text": _content(text, MAX_BODY_FIELD_CHARS, text_provenance),
                "html": _content(html, MAX_BODY_FIELD_CHARS, "message.body.html"),
            },
        }
    people = set(from_emails + to_emails + cc_emails + bcc_emails)
    field_content_truncated = projection == "full" and any(
        field_value["coverage"] == "truncated"
        for field_value in (
            [item["content"]["subject"]]
            + [item["content"]["body"]["text"], item["content"]["body"]["html"]]
            + [
                address["display_name"]
                for values in item["addresses"].values()
                for address in values
            ]
            + [attachment["filename"] for attachment in item["attachments"]]
        )
    )
    return (
        item,
        people,
        participant_truncated,
        attachments_truncated,
        field_content_truncated,
    )


def _scan_account(
    request: ScanRequest, account: str, account_config: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    source = _source_path(account_config)
    since_ms = _epoch_ms(request.since)
    reasons: list[str] = []
    records: list[dict[str, Any]] = []
    inspected = 0
    eligible = 0
    thread_rows = 0
    with transient_connection(source) as (conn, snapshot_bytes):
        params: list[Any] = [since_ms]
        selector_sql = ""
        if request.threads:
            selector_sql = (
                " AND thread_id IN (" + ",".join("?" for _ in request.threads) + ")"
            )
            params.extend(request.threads)
        params.append(MAX_THREADS_PER_ACCOUNT + 1)
        sql = (
            "SELECT thread_id, json, sort FROM threads WHERE sort >= ?"
            + selector_sql
            + " ORDER BY sort DESC, thread_id ASC LIMIT ?"
        )
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        except sqlite3.Error as exc:
            raise ReaderError(
                "CACHE_SCHEMA_UNSUPPORTED",
                "The selected account cache schema is unsupported",
                cls="conflict",
            ) from exc
        if len(rows) > MAX_THREADS_PER_ACCOUNT:
            reasons.append("THREAD_LIMIT")
            rows = rows[:MAX_THREADS_PER_ACCOUNT]
        seen_message_ids: set[str] = set()
        for row_index, row in enumerate(rows):
            thread_rows += 1
            thread_id = _strict_string(row[0], "thread id", maximum=MAX_ID_CHARS)
            raw_json = row[1]
            if (
                not isinstance(raw_json, str)
                or not _valid_text(raw_json)
                or len(raw_json.encode("utf-8")) > MAX_ROW_JSON_BYTES
            ):
                raise ReaderError(
                    "MALFORMED_CACHE_JSON",
                    "A cached thread JSON record is invalid or oversized",
                    cls="conflict",
                )
            try:
                thread = json.loads(raw_json)
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise ReaderError(
                    "MALFORMED_CACHE_JSON",
                    "A cached thread JSON record is malformed",
                    cls="conflict",
                ) from exc
            if not isinstance(thread, dict) or not isinstance(
                thread.get("messages"), list
            ):
                raise ReaderError(
                    "MALFORMED_CACHE_JSON",
                    "A cached thread JSON record has an invalid message list",
                    cls="conflict",
                )
            messages = thread["messages"]
            inspected_before_thread = inspected
            for raw_message in messages:
                if inspected >= MAX_MESSAGES_INSPECTED_PER_ACCOUNT:
                    if "MESSAGE_SCAN_LIMIT" not in reasons:
                        reasons.append("MESSAGE_SCAN_LIMIT")
                    break
                inspected += 1
                (
                    item,
                    participant_emails,
                    participant_truncated,
                    attachment_truncated,
                    field_content_truncated,
                ) = _message(raw_message, account, thread_id, request.projection)
                message_id = item["message_id"]
                identity = f"{thread_id}\0{message_id}"
                if identity in seen_message_ids:
                    raise ReaderError(
                        "MALFORMED_CACHE_JSON",
                        "A cached thread contains duplicate message identifiers",
                        cls="conflict",
                    )
                seen_message_ids.add(identity)
                epoch = item["date_epoch_ms"]
                if epoch < since_ms or epoch >= _epoch_ms(request.before):
                    continue
                if request.people and not participant_emails.intersection(
                    request.people
                ):
                    continue
                eligible += 1
                records.append(item)
                if participant_truncated and "PARTICIPANT_LIMIT" not in reasons:
                    reasons.append("PARTICIPANT_LIMIT")
                if attachment_truncated and "ATTACHMENT_LIMIT" not in reasons:
                    reasons.append("ATTACHMENT_LIMIT")
                if field_content_truncated and "FIELD_CONTENT_LIMIT" not in reasons:
                    reasons.append("FIELD_CONTENT_LIMIT")
            if inspected >= MAX_MESSAGES_INSPECTED_PER_ACCOUNT:
                unprocessed_messages = inspected - inspected_before_thread < len(
                    messages
                )
                unprocessed_threads = row_index + 1 < len(rows)
                if (
                    unprocessed_messages or unprocessed_threads
                ) and "MESSAGE_SCAN_LIMIT" not in reasons:
                    reasons.append("MESSAGE_SCAN_LIMIT")
                break

    records.sort(
        key=lambda item: (
            -item["date_epoch_ms"],
            item["account_id"],
            item["thread_id"],
            item["message_id"],
        )
    )
    if len(records) > MAX_MESSAGES_PER_ACCOUNT:
        reasons.append("ACCOUNT_MESSAGE_LIMIT")
        records = records[:MAX_MESSAGES_PER_ACCOUNT]
    facts = {
        "account_id": account,
        "snapshot_bytes": snapshot_bytes,
        "threads_observed": thread_rows,
        "messages_inspected": inspected,
        "messages_eligible": eligible,
        "messages_emitted": len(records),
        "coverage": "truncated" if reasons else "complete",
        "truncation_reasons": reasons,
        "query": {
            "thread_sort_since_pushed_down": True,
            "thread_ids_pushed_down": bool(request.threads),
            "before_applied_to_thread_sort": False,
            "spam_trash_included": True,
            "fts_queried": False,
        },
        "snapshot": {
            "source_read_only": True,
            "sqlite_read_only": True,
            "sqlite_immutable": True,
            "sqlite_query_only": True,
            "unlinked_during_query": True,
            "source_identity_stable": True,
        },
    }
    return records, facts, reasons


def _apply_global_caps(records: list[dict[str, Any]], projection: str) -> list[str]:
    reasons: list[str] = []
    participants_left = MAX_PARTICIPANTS
    attachments_left = MAX_ATTACHMENTS
    content_left = MAX_CONTENT_CHARS
    for record in records:
        addresses = record["addresses"]
        for field in ("from", "to", "cc", "bcc"):
            values = addresses[field]
            if len(values) > participants_left:
                addresses[field] = values[:participants_left]
                record["addresses_coverage"] = "truncated"
                if "GLOBAL_PARTICIPANT_LIMIT" not in reasons:
                    reasons.append("GLOBAL_PARTICIPANT_LIMIT")
            participants_left -= len(addresses[field])
            participants_left = max(0, participants_left)
        attachments = record["attachments"]
        if len(attachments) > attachments_left:
            record["attachments"] = attachments[:attachments_left]
            record["attachments_coverage"] = "truncated"
            if "GLOBAL_ATTACHMENT_LIMIT" not in reasons:
                reasons.append("GLOBAL_ATTACHMENT_LIMIT")
        attachments_left -= len(record["attachments"])
        attachments_left = max(0, attachments_left)
        if projection == "full":
            fields: list[dict[str, Any]] = [
                record["content"]["subject"],
                record["content"]["body"]["text"],
                record["content"]["body"]["html"],
            ]
            for values in addresses.values():
                fields.extend(value["display_name"] for value in values)
            fields.extend(value["filename"] for value in record["attachments"])
            for field in fields:
                value = field["value"]
                if not isinstance(value, str):
                    continue
                if len(value) > content_left:
                    field["value"] = value[:content_left]
                    field["coverage"] = "truncated"
                    if "GLOBAL_CONTENT_LIMIT" not in reasons:
                        reasons.append("GLOBAL_CONTENT_LIMIT")
                content_left -= len(field["value"])
                content_left = max(0, content_left)
    return reasons


def contract_limits() -> dict[str, int]:
    """Return every fixed resource and representation cap in the contract."""
    return {
        "source_bytes": MAX_SOURCE_BYTES,
        "selector_values_per_category": MAX_SELECTOR_VALUES,
        "thread_json_bytes": MAX_ROW_JSON_BYTES,
        "threads_per_account": MAX_THREADS_PER_ACCOUNT,
        "messages_inspected_per_account": MAX_MESSAGES_INSPECTED_PER_ACCOUNT,
        "messages_per_account": MAX_MESSAGES_PER_ACCOUNT,
        "records": MAX_RECORDS,
        "participants_per_message": MAX_PARTICIPANTS_PER_MESSAGE,
        "participants_global": MAX_PARTICIPANTS,
        "attachments_per_message": MAX_ATTACHMENTS_PER_MESSAGE,
        "attachments_global": MAX_ATTACHMENTS,
        "id_characters": MAX_ID_CHARS,
        "email_characters": MAX_EMAIL_CHARS,
        "label_and_media_type_characters": MAX_LABEL_CHARS,
        "subject_characters": MAX_SUBJECT_CHARS,
        "body_field_characters": MAX_BODY_FIELD_CHARS,
        "display_name_characters": MAX_NAME_CHARS,
        "filename_characters": MAX_FILENAME_CHARS,
        "content_characters_global": MAX_CONTENT_CHARS,
        "output_bytes": MAX_OUTPUT_BYTES,
    }


def scan(
    *,
    since: str,
    before: str,
    accounts: list[str],
    projection: str,
    threads: list[str],
    people: list[str],
) -> dict[str, Any]:
    """Run one bounded local-cache scan for each selected account."""
    try:
        request = validate_request(
            since=since,
            before=before,
            accounts=accounts,
            projection=projection,
            threads=threads,
            people=people,
        )
        configured = _configured_accounts()
        records: list[dict[str, Any]] = []
        account_facts: list[dict[str, Any]] = []
        reasons: list[str] = []
        for account in request.accounts:
            account_records, facts, account_reasons = _scan_account(
                request, account, configured[account]
            )
            records.extend(account_records)
            account_facts.append(facts)
            reasons.extend(
                reason for reason in account_reasons if reason not in reasons
            )
        records.sort(
            key=lambda item: (
                -item["date_epoch_ms"],
                item["account_id"],
                item["thread_id"],
                item["message_id"],
            )
        )
        if len(records) > MAX_RECORDS:
            records = records[:MAX_RECORDS]
            reasons.append("RECORD_LIMIT")
        reasons.extend(
            reason
            for reason in _apply_global_caps(records, request.projection)
            if reason not in reasons
        )
        data: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "projection": request.projection,
            "window": {
                "since": request.since.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
                "before": request.before.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
            },
            "selectors": {
                "accounts": list(request.accounts),
                "threads": list(request.threads),
                "people": list(request.people),
            },
            "records": records,
            "record_count": len(records),
            "coverage": "truncated" if reasons else "complete",
            "truncation_reasons": reasons,
            "accounts": account_facts,
            "cursor": None,
            "limits": contract_limits(),
        }
        for facts in account_facts:
            facts["records_emitted"] = sum(
                1 for record in records if record["account_id"] == facts["account_id"]
            )
        while (
            records
            and len(
                json.dumps(
                    ok(COMMAND, data, warnings=["LOCAL_CACHE_COVERAGE_ONLY"]),
                    ensure_ascii=False,
                ).encode("utf-8")
            )
            > MAX_OUTPUT_BYTES
        ):
            removed = records.pop()
            data["record_count"] = len(records)
            data["coverage"] = "truncated"
            if "OUTPUT_BYTE_LIMIT" not in reasons:
                reasons.append("OUTPUT_BYTE_LIMIT")
            for facts in account_facts:
                if facts["account_id"] == removed["account_id"]:
                    facts["records_emitted"] -= 1
                    break
        return ok(COMMAND, data, warnings=["LOCAL_CACHE_COVERAGE_ONLY"])
    except ReaderError as exc:
        return fail(
            COMMAND,
            [error(exc.cls, exc.code, exc.retryable, exc.hint)],
            warnings=["LOCAL_CACHE_COVERAGE_ONLY"],
        )
    except (OSError, sqlite3.Error):
        return fail(
            COMMAND,
            [
                error(
                    "conflict",
                    "CACHE_READ_FAILED",
                    True,
                    "The selected account cache could not be read safely",
                )
            ],
            warnings=["LOCAL_CACHE_COVERAGE_ONLY"],
        )
    except Exception:  # noqa: BLE001 - fail-closed JSON boundary
        # The production contract must fail closed without serializing an
        # exception that could contain a local path or cached content.
        return fail(
            COMMAND,
            [
                error(
                    "conflict",
                    "READER_FAILED",
                    False,
                    "The local cache scan could not be completed safely",
                )
            ],
            warnings=["LOCAL_CACHE_COVERAGE_ONLY"],
        )
