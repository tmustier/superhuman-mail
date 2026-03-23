"""Tests for multi-account setup disambiguation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from superhuman_mail.setup import extract_accounts, extract_db_file, extract_email, extract_google_id, extract_team_id


def _write_config(path: Path, tab_paths: list[str]) -> None:
    data = {
        "state": {
            "windows": [
                {"tabs": [{"path": p} for p in tab_paths]}
            ]
        }
    }
    path.write_text(json.dumps(data))


def _write_wrapped_db(fs_dir: Path, filename: str, owner_email: str) -> None:
    raw = fs_dir / f"{filename}.raw.sqlite3"
    conn = sqlite3.connect(raw)
    conn.execute("CREATE TABLE general (key TEXT, json TEXT)")
    conn.execute(
        "INSERT INTO general (key, json) VALUES (?, ?)",
        ("teamMembers", json.dumps({"user": {"emailAddress": owner_email}})),
    )
    conn.commit()
    conn.close()

    wrapped = fs_dir / filename
    wrapped.write_bytes(b"\x00" * 4096 + raw.read_bytes())
    raw.unlink()


class TestExtractEmail:
    def test_single_account(self, tmp_path):
        config_json = tmp_path / "config.json"
        _write_config(config_json, ["/mail/one@example.com/inbox"])

        with patch("superhuman_mail.setup._CONFIG_JSON", config_json):
            assert extract_email() == "one@example.com"

    def test_multiple_accounts_require_explicit_email(self, tmp_path):
        config_json = tmp_path / "config.json"
        _write_config(config_json, [
            "/mail/one@example.com/inbox",
            "/mail/two@example.com/inbox",
        ])

        with patch("superhuman_mail.setup._CONFIG_JSON", config_json):
            with pytest.raises(RuntimeError, match="Multiple Superhuman accounts detected"):
                extract_email()

    def test_preferred_account_is_selected_case_insensitively(self, tmp_path):
        config_json = tmp_path / "config.json"
        _write_config(config_json, [
            "/mail/one@example.com/inbox",
            "/mail/two@example.com/inbox",
        ])

        with patch("superhuman_mail.setup._CONFIG_JSON", config_json):
            assert extract_email("TWO@example.com") == "two@example.com"


class TestExtractDbFile:
    def test_multiple_db_files_require_email(self, tmp_path):
        _write_wrapped_db(tmp_path, "00000001", "one@example.com")
        _write_wrapped_db(tmp_path, "00000002", "two@example.com")

        with patch("superhuman_mail.setup._FS_DIR", tmp_path):
            with pytest.raises(RuntimeError, match="Multiple SQLite DB files detected"):
                extract_db_file()

    def test_selected_email_picks_matching_db_file(self, tmp_path):
        _write_wrapped_db(tmp_path, "00000001", "one@example.com")
        _write_wrapped_db(tmp_path, "00000002", "two@example.com")

        with patch("superhuman_mail.setup._FS_DIR", tmp_path):
            assert extract_db_file("two@example.com") == "00000002"

    def test_single_db_file_with_mismatched_owner_raises(self, tmp_path):
        _write_wrapped_db(tmp_path, "00000001", "one@example.com")

        with patch("superhuman_mail.setup._FS_DIR", tmp_path):
            with pytest.raises(RuntimeError, match="belongs to one@example.com"):
                extract_db_file("two@example.com")

    def test_extract_accounts_discovers_all_mapped_mailboxes(self, tmp_path):
        _write_wrapped_db(tmp_path, "00000002", "two@example.com")
        _write_wrapped_db(tmp_path, "00000001", "one@example.com")

        with patch("superhuman_mail.setup._FS_DIR", tmp_path):
            assert extract_accounts() == [
                {"email": "one@example.com", "db_file": "00000001"},
                {"email": "two@example.com", "db_file": "00000002"},
            ]

    def test_extract_accounts_omits_ambiguous_secondary_mailboxes(self, tmp_path):
        _write_wrapped_db(tmp_path, "00000001", "one@example.com")
        _write_wrapped_db(tmp_path, "00000002", "one@example.com")
        _write_wrapped_db(tmp_path, "00000003", "two@example.com")

        with patch("superhuman_mail.setup._FS_DIR", tmp_path):
            assert extract_accounts() == [
                {"email": "two@example.com", "db_file": "00000003"},
            ]


class TestExtractGoogleId:
    def test_matching_google_id_is_selected_for_email(self):
        def fake_request(email: str, google_id: str, device_id: str, version: str) -> dict[str, object]:
            if google_id == "2222222222":
                return {"authData": {}}
            raise RuntimeError("wrong account")

        with patch("superhuman_mail.setup.extract_google_ids", return_value=["1111111111", "2222222222"]):
            with patch("superhuman_mail.setup._request_auth_data", side_effect=fake_request):
                assert extract_google_id("two@example.com", "device-1", "2026-03-23T00:00:00Z") == "2222222222"

    def test_single_google_id_is_still_validated(self):
        with patch("superhuman_mail.setup.extract_google_ids", return_value=["1111111111"]):
            with patch("superhuman_mail.setup._request_auth_data", side_effect=RuntimeError("wrong account")):
                with pytest.raises(RuntimeError, match="none matched"):
                    extract_google_id("missing@example.com", "device-1", "2026-03-23T00:00:00Z")

    def test_raises_when_no_google_id_matches_email(self):
        with patch("superhuman_mail.setup.extract_google_ids", return_value=["1111111111", "2222222222"]):
            with patch("superhuman_mail.setup._request_auth_data", side_effect=RuntimeError("wrong account")):
                with pytest.raises(RuntimeError, match="none matched"):
                    extract_google_id("missing@example.com", "device-1", "2026-03-23T00:00:00Z")


class TestExtractTeamId:
    def test_single_team_id_returns_value(self):
        with patch("superhuman_mail.setup._read_leveldb_strings", return_value=["team_abc123456789012", "team_abc123456789012"]):
            assert extract_team_id() == "team_abc123456789012"

    def test_multiple_team_ids_raise(self):
        with patch("superhuman_mail.setup._read_leveldb_strings", return_value=["team_abc123456789012", "team_def123456789012"]):
            with pytest.raises(RuntimeError, match="Multiple Superhuman team IDs detected"):
                extract_team_id()
