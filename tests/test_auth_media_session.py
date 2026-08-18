"""Tests for account-scoped Superhuman media session extraction."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from superhuman_mail import _auth


def _cookie_db(base: Path) -> None:
    connection = sqlite3.connect(base / "Cookies")
    connection.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB)"
    )
    connection.executemany(
        "INSERT INTO cookies VALUES (?, ?, ?)",
        [
            ("media.superhuman.com", "1111111111", b"secret-one"),
            ("media.superhuman.com", "2222222222", b"secret-two"),
            ("media.superhuman.com", "device-id", b"not-an-account"),
            ("accounts.superhuman.com", "3333333333", b"wrong-host"),
        ],
    )
    connection.commit()
    connection.close()


def test_media_credentials_are_account_only_preferred_and_non_printable(
    tmp_path: Path,
) -> None:
    _cookie_db(tmp_path)
    with patch("superhuman_mail._auth._config.superhuman_base", return_value=tmp_path):
        with patch("superhuman_mail._auth._config.api", return_value="2222222222"):
            with patch(
                "superhuman_mail._auth._get_encryption_key", return_value=b"key"
            ):
                with patch(
                    "superhuman_mail._auth._decrypt_cookie",
                    side_effect=lambda encrypted, _key: encrypted.decode(),
                ):
                    credentials = _auth.media_session_credentials()

    assert [item.provider_id for item in credentials] == ["2222222222", "1111111111"]
    assert [item.cookie_value for item in credentials] == ["secret-two", "secret-one"]
    assert "secret-one" not in repr(credentials)
    assert "secret-two" not in repr(credentials)


def test_missing_media_credentials_fails_without_secret_output(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "Cookies")
    connection.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB)"
    )
    connection.execute(
        "INSERT INTO cookies VALUES (?, ?, ?)",
        ("media.superhuman.com", "device-id", b"secret-device"),
    )
    connection.commit()
    connection.close()

    with patch("superhuman_mail._auth._config.superhuman_base", return_value=tmp_path):
        try:
            _auth.media_session_credentials()
        except RuntimeError as exc:
            assert "secret-device" not in str(exc)
            assert "media session cookie not found" in str(exc)
        else:
            raise AssertionError("missing account-scoped media cookie was accepted")
