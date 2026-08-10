"""Synthetic contract and safety tests for ``shm reader scan``.

No live Superhuman data is read by this suite.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from superhuman_mail import _config, reader
from superhuman_mail.cli import main

PREFIX = b"W" * reader.WRAPPER_BYTES


def address(email: str, name: str = "Secret Name") -> dict[str, str]:
    return {"email": email, "name": name}


def message(
    message_id: str,
    date: int,
    *,
    sender: str = "sender@example.com",
    to: list[dict[str, str]] | None = None,
    cc: list[dict[str, str]] | None = None,
    bcc: list[dict[str, str]] | None = None,
    labels: list[str] | None = None,
    subject: str = "Secret Subject",
    body: Any = None,
    snippet: str = "Secret Snippet",
    attachments: list[dict[str, Any]] | None = None,
    is_draft: bool = False,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": message_id,
        "date": date,
        "from": address(sender),
        "to": to if to is not None else [address("recipient@example.com")],
        "cc": cc if cc is not None else [],
        "bcc": bcc if bcc is not None else [],
        "labelIds": labels if labels is not None else ["INBOX", "UNREAD"],
        "subject": subject,
        "body": body if body is not None else {"text": "Secret Body", "html": "<p>Secret Body</p>"},
        "snippet": snippet,
        "attachments": attachments if attachments is not None else [{"id": "a1", "name": "secret.pdf", "type": "application/pdf", "size": 42}],
    }
    if is_draft:
        value["isDraft"] = True
        value["draftId"] = "draft-1"
    return value


def create_wrapped_db(path: Path, rows: list[tuple[str, Any, int]]) -> None:
    sqlite_path = path.with_suffix(".sqlite")
    conn = sqlite3.connect(sqlite_path)
    conn.execute("CREATE TABLE threads (thread_id TEXT PRIMARY KEY, json TEXT, sort INTEGER, in_spam_trash INTEGER DEFAULT 0)")
    for thread_id, payload, sort_value in rows:
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        conn.execute("INSERT INTO threads(thread_id, json, sort, in_spam_trash) VALUES (?, ?, ?, 1)", (thread_id, raw, sort_value))
    conn.commit()
    conn.close()
    path.write_bytes(PREFIX + sqlite_path.read_bytes())
    sqlite_path.unlink()


@pytest.fixture
def cache_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path]]:
    base = tmp_path / "Superhuman"
    cache_dir = base / "File System/000/t/00"
    cache_dir.mkdir(parents=True)
    monkeypatch.setattr(_config, "superhuman_base", lambda: base)
    monkeypatch.setattr(_config, "accounts", lambda: [
        {"email": "a@example.com", "db_file": "a.cache"},
        {"email": "b@example.com", "db_file": "b.cache"},
    ])
    yield cache_dir / "a.cache", cache_dir / "b.cache"


def run_scan(*, accounts: list[str] | None = None, projection: str = "metadata", threads: list[str] | None = None, people: list[str] | None = None) -> dict[str, Any]:
    return reader.scan(
        since="2026-01-01T00:00:00Z",
        before="2026-01-02T00:00:00Z",
        accounts=accounts if accounts is not None else ["a@example.com"],
        projection=projection,
        threads=threads or [],
        people=people or [],
    )


def error_code(result: dict[str, Any]) -> str:
    return str(result["errors"][0]["code"])


def all_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(key)
            found.update(all_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(all_keys(child))
    return found


class TestRequestValidation:
    @pytest.mark.parametrize("value", ["2026-01-01", "2026-01-01T00:00:00+00:00", "2026-1-1T00:00:00Z", "not-a-date"])
    def test_rejects_non_exact_utc_z(self, cache_config: tuple[Path, Path], value: str) -> None:
        result = reader.scan(since=value, before="2026-01-02T00:00:00Z", accounts=["a@example.com"], projection="metadata", threads=[], people=[])
        assert error_code(result) == "INVALID_TIME"

    def test_rejects_inverted_or_empty_window(self, cache_config: tuple[Path, Path]) -> None:
        result = reader.scan(since="2026-01-02T00:00:00Z", before="2026-01-02T00:00:00Z", accounts=["a@example.com"], projection="metadata", threads=[], people=[])
        assert error_code(result) == "INVALID_RANGE"

    @pytest.mark.parametrize(
        ("accounts", "threads", "people", "code"),
        [
            (["a@example.com", "a@example.com"], [], [], "DUPLICATE_SELECTOR"),
            (["missing@example.com"], [], [], "UNKNOWN_ACCOUNT"),
            (["a@example.com"], ["thread 1"], [], "INVALID_SELECTOR"),
            (["a@example.com"], [], ["bad"], "INVALID_SELECTOR"),
            (["a@example.com"], [], ["A@EXAMPLE.COM", "a@example.com"], "DUPLICATE_SELECTOR"),
        ],
    )
    def test_rejects_unknown_malformed_and_duplicate_selectors(self, cache_config: tuple[Path, Path], accounts: list[str], threads: list[str], people: list[str], code: str) -> None:
        result = reader.scan(since="2026-01-01T00:00:00Z", before="2026-01-02T00:00:00Z", accounts=accounts, projection="metadata", threads=threads, people=people)
        assert error_code(result) == code

    def test_omitted_accounts_means_all_configured_accounts(self, cache_config: tuple[Path, Path]) -> None:
        first, second = cache_config
        payload = {"messages": [message("m", 1767225600000)]}
        create_wrapped_db(first, [("t", payload, 1767225600000)])
        create_wrapped_db(second, [("t", payload, 1767225600000)])
        result = run_scan(accounts=[])
        assert result["status"] == "succeeded"
        assert result["data"]["selectors"]["accounts"] == ["a@example.com", "b@example.com"]


class TestSnapshotSafety:
    def test_snapshot_is_unlinked_read_only_immutable_and_query_only(self, cache_config: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = cache_config
        create_wrapped_db(source, [("t", {"messages": [message("m", 1767225600000)]}, 1767225600000)])
        monkeypatch.setattr(reader.tempfile, "gettempdir", lambda: str(tmp_path))
        before_mode = source.stat().st_mode
        with reader.transient_connection(source) as (conn, copied):
            assert copied == source.stat().st_size - reader.WRAPPER_BYTES
            assert list(tmp_path.glob(".shm-reader-*")) == []
            assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE forbidden(value TEXT)")
        assert list(tmp_path.glob(".shm-reader-*")) == []
        assert source.stat().st_mode == before_mode

    def test_temp_mode_is_0600_and_cleanup_happens_on_open_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "broken.cache"
        source.write_bytes(PREFIX + b"not sqlite")
        monkeypatch.setattr(reader.tempfile, "gettempdir", lambda: str(tmp_path))
        modes: list[int] = []
        real_connect = reader.sqlite3.connect

        def observe_connect(database: str, *, uri: bool) -> sqlite3.Connection:
            snapshot = Path(database.removeprefix("file:").split("?", 1)[0])
            modes.append(snapshot.stat().st_mode & 0o777)
            return real_connect(database, uri=uri)

        monkeypatch.setattr(reader.sqlite3, "connect", observe_connect)
        with pytest.raises(reader.ReaderError), reader.transient_connection(source):
            pass
        assert modes == [0o600]
        assert list(tmp_path.glob(".shm-reader-*")) == []

    def test_source_change_fails_closed_and_deletes_snapshot(self, cache_config: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = cache_config
        create_wrapped_db(source, [("t", {"messages": [message("m", 1767225600000)]}, 1767225600000)])
        monkeypatch.setattr(reader.tempfile, "gettempdir", lambda: str(tmp_path))
        real_read = reader.os.read
        changed = False

        def changing_read(fd: int, count: int) -> bytes:
            nonlocal changed
            block = real_read(fd, count)
            if block == b"" and not changed:
                changed = True
                with source.open("ab") as handle:
                    handle.write(b"x")
            return block

        monkeypatch.setattr(reader.os, "read", changing_read)
        result = run_scan()
        assert error_code(result) == "SOURCE_CHANGED"
        assert result["errors"][0]["retryable"] is True
        assert list(tmp_path.glob(".shm-reader-*")) == []


class TestScanSemantics:
    def test_exact_window_thread_and_person_semantics_include_spam_trash(self, cache_config: tuple[Path, Path]) -> None:
        source, _ = cache_config
        start = 1767225600000
        end = 1767312000000
        rows = [
            ("wanted", {"messages": [
                message("at-start", start, cc=[address("Target@Example.com")]),
                message("middle-other", start + 1, sender="other@example.com"),
                message("at-end", end, bcc=[address("target@example.com")]),
            ]}, end + 999999),
            ("other-thread", {"messages": [message("other", start + 2, sender="target@example.com")]}, start + 2),
        ]
        create_wrapped_db(source, rows)
        result = run_scan(threads=["wanted"], people=["target@example.com"])
        assert result["status"] == "succeeded"
        assert [item["message_id"] for item in result["data"]["records"]] == ["at-start"]
        facts = result["data"]["accounts"][0]
        assert facts["query"] == {
            "thread_sort_since_pushed_down": True,
            "thread_ids_pushed_down": True,
            "before_applied_to_thread_sort": False,
            "spam_trash_included": True,
            "fts_queried": False,
        }

    def test_deterministic_global_order(self, cache_config: tuple[Path, Path]) -> None:
        first, second = cache_config
        when = 1767225600000
        create_wrapped_db(first, [("z-thread", {"messages": [message("b", when), message("a", when)]}, when)])
        create_wrapped_db(second, [("a-thread", {"messages": [message("z", when + 1)]}, when + 1)])
        result = run_scan(accounts=[])
        identities = [(item["date_epoch_ms"], item["account_id"], item["thread_id"], item["message_id"]) for item in result["data"]["records"]]
        assert identities == [
            (when + 1, "b@example.com", "a-thread", "z"),
            (when, "a@example.com", "z-thread", "a"),
            (when, "a@example.com", "z-thread", "b"),
        ]

    def test_draft_read_label_attachment_and_bcc_facts(self, cache_config: tuple[Path, Path]) -> None:
        source, _ = cache_config
        create_wrapped_db(source, [("t", {"messages": [message("draft", 1767225600000, is_draft=True, bcc=None)]}, 1767225600000)])
        record = run_scan()["data"]["records"][0]
        assert record["draft"] == {"is_draft": True, "evidence": ["message.isDraft", "message.draftId"]}
        assert record["read"] == {"is_unread": True, "evidence": "message.labelIds"}
        assert record["addresses"]["bcc"] == []
        assert record["attachments"] == [{"size_bytes": 42, "media_type": "application/pdf", "attachment_id": "a1"}]


class TestProjectionAndCaps:
    def test_metadata_recursively_excludes_content_and_fts_fields(self, cache_config: tuple[Path, Path]) -> None:
        source, _ = cache_config
        create_wrapped_db(source, [("t", {"messages": [message("m", 1767225600000)]}, 1767225600000)])
        result = run_scan()
        assert result["status"] == "succeeded"
        forbidden = {"subject", "body", "snippet", "display_name", "filename"}
        assert not (all_keys(result) & forbidden)
        serialized = json.dumps(result)
        for secret in ("Secret Subject", "Secret Body", "Secret Snippet", "Secret Name", "secret.pdf"):
            assert secret not in serialized
        assert result["warnings"] == ["LOCAL_CACHE_COVERAGE_ONLY"]

    def test_full_content_has_provenance_and_complete_unavailable_truncated_states(self, cache_config: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = cache_config
        monkeypatch.setattr(reader, "MAX_SUBJECT_CHARS", 3)
        payload = {"messages": [
            message("direct", 1767225600002, subject="abcdef", body={"text": "real", "html": ""}),
            message("missing", 1767225600001, subject="", body={}, snippet="must never become body"),
        ]}
        create_wrapped_db(source, [("t", payload, 1767225600002)])
        records = run_scan(projection="full")["data"]["records"]
        assert records[0]["content"]["subject"] == {"value": "abc", "coverage": "truncated", "provenance": "message.subject"}
        assert records[0]["content"]["body"]["text"] == {"value": "real", "coverage": "complete", "provenance": "message.body.text"}
        assert records[0]["content"]["body"]["html"] == {"value": None, "coverage": "unavailable", "provenance": "none"}
        assert records[1]["content"]["body"]["text"]["coverage"] == "unavailable"
        assert "must never become body" not in json.dumps(records)
        assert records[0]["addresses"]["from"][0]["display_name"]["coverage"] == "complete"
        assert records[0]["attachments"][0]["filename"]["coverage"] == "complete"

    def test_global_content_and_output_caps_are_explicit(self, cache_config: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = cache_config
        monkeypatch.setattr(reader, "MAX_CONTENT_CHARS", 5)
        payload = {"messages": [message(f"m-{index}", 1767225600000 + index, body={"text": "abcdefghij"}) for index in range(8)]}
        create_wrapped_db(source, [("t", payload, 1767225600007)])
        content_result = run_scan(projection="full")
        assert "GLOBAL_CONTENT_LIMIT" in content_result["data"]["truncation_reasons"]
        assert content_result["data"]["records"][0]["content"]["subject"]["value"] == "Secre"
        assert content_result["data"]["records"][0]["content"]["subject"]["coverage"] == "truncated"

        # Pick a byte ceiling above the empty response but below the response
        # with all records, proving whole DTOs are removed rather than sliced.
        monkeypatch.setattr(reader, "MAX_CONTENT_CHARS", 2 * 1024 * 1024)
        monkeypatch.setattr(reader, "MAX_OUTPUT_BYTES", 4_000)
        output_result = run_scan()
        assert "OUTPUT_BYTE_LIMIT" in output_result["data"]["truncation_reasons"]
        assert output_result["data"]["record_count"] < 8
        assert all(set(record) >= {"account_id", "thread_id", "message_id", "addresses", "attachments"} for record in output_result["data"]["records"])

    def test_participant_attachment_record_and_record_caps_are_explicit(self, cache_config: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = cache_config
        monkeypatch.setattr(reader, "MAX_PARTICIPANTS_PER_MESSAGE", 1)
        monkeypatch.setattr(reader, "MAX_ATTACHMENTS_PER_MESSAGE", 1)
        monkeypatch.setattr(reader, "MAX_RECORDS", 1)
        first = message(
            "new",
            1767225600002,
            to=[address("one@example.com"), address("two@example.com")],
            attachments=[{"id": "one", "size": 1}, {"id": "two", "size": 2}],
        )
        second = message("old", 1767225600001)
        create_wrapped_db(source, [("t", {"messages": [first, second]}, 1767225600002)])
        result = run_scan()
        assert result["data"]["record_count"] == 1
        record = result["data"]["records"][0]
        assert record["addresses_coverage"] == "truncated"
        assert record["attachments_coverage"] == "truncated"
        assert record["addresses"]["to"] == [{"email": "one@example.com"}]
        assert record["attachments"] == [{"size_bytes": 1, "media_type": None, "attachment_id": "one"}]
        assert result["data"]["coverage"] == "truncated"
        assert set(result["data"]["truncation_reasons"]) >= {"PARTICIPANT_LIMIT", "ATTACHMENT_LIMIT", "RECORD_LIMIT"}
        assert result["data"]["cursor"] is None


class TestFailuresAndCLI:
    @pytest.mark.parametrize("payload", ["{bad json", json.dumps({"messages": [42]}), json.dumps({"not_messages": []})])
    def test_malformed_json_fails_whole_command_privately(self, cache_config: tuple[Path, Path], payload: str) -> None:
        source, _ = cache_config
        create_wrapped_db(source, [("t", payload, 1767225600000)])
        result = run_scan()
        assert result["status"] == "failed"
        assert error_code(result) == "MALFORMED_CACHE_JSON"
        assert result["data"] is None
        assert str(source) not in json.dumps(result)

    def test_metadata_executes_one_threads_query_and_never_fts(self, cache_config: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = cache_config
        create_wrapped_db(source, [("t", {"messages": [message("m", 1767225600000)]}, 1767225600000)])
        statements: list[str] = []
        real_connect = reader.sqlite3.connect

        def traced_connect(database: str, *, uri: bool) -> sqlite3.Connection:
            conn = real_connect(database, uri=uri)
            conn.set_trace_callback(statements.append)
            return conn

        monkeypatch.setattr(reader.sqlite3, "connect", traced_connect)
        assert run_scan()["status"] == "succeeded"
        select_threads = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT THREAD_ID")]
        assert len(select_threads) == 1
        assert all("thread_search" not in sql.casefold() and "fts" not in sql.casefold() for sql in statements)

    def test_malformed_database_and_selected_account_failure_are_whole_command(self, cache_config: tuple[Path, Path]) -> None:
        first, second = cache_config
        create_wrapped_db(first, [("t", {"messages": [message("m", 1767225600000)]}, 1767225600000)])
        second.write_bytes(PREFIX + b"invalid")
        result = run_scan(accounts=[])
        assert result["status"] == "failed"
        assert result["data"] is None
        assert error_code(result) in {"MALFORMED_CACHE", "CACHE_SCHEMA_UNSUPPORTED"}
        assert str(second) not in json.dumps(result)

    def test_schema_and_reader_dispatch(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["schema", "reader.scan"]) == 0
        schema = json.loads(capsys.readouterr().out)
        assert schema["data"]["contract_version"] == "1.0"
        expected = {"status": "succeeded", "command": "reader.scan", "data": {"record_count": 0}, "errors": [], "warnings": ["LOCAL_CACHE_COVERAGE_ONLY"]}
        with patch("superhuman_mail.cli._reader.scan", return_value=expected) as scan:
            code = main(["reader", "scan", "--since", "2026-01-01T00:00:00Z", "--before", "2026-01-02T00:00:00Z", "--account", "a@example.com", "--thread", "t", "--person", "p@example.com"])
        assert code == 0
        scan.assert_called_once_with(since="2026-01-01T00:00:00Z", before="2026-01-02T00:00:00Z", accounts=["a@example.com"], projection="metadata", threads=["t"], people=["p@example.com"])
        assert json.loads(capsys.readouterr().out)["command"] == "reader.scan"

    def test_cli_missing_required_argument_is_reader_scan_envelope(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main(["reader", "scan", "--since", "2026-01-01T00:00:00Z"])
        result = json.loads(capsys.readouterr().out)
        assert result["command"] == "reader.scan"
        assert error_code(result) == "INVALID_ARGS"
