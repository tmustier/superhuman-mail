from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from superhuman_mail import _auth


def test_runtime_provider_token_is_read_from_stdin_not_environment(monkeypatch):
    _auth._token_cache.clear()
    monkeypatch.setenv("SHM_AUTH_TOKEN_STDIN", "1")
    with patch("sys.stdin", io.StringIO("synthetic-runtime-token\n")):
        assert _auth._get_id_token() == "synthetic-runtime-token"
        assert _auth._get_id_token() == "synthetic-runtime-token"
    assert "synthetic-runtime-token" not in str(dict(__import__("os").environ))


def test_runtime_provider_token_rejects_whitespace(monkeypatch):
    _auth._token_cache.clear()
    monkeypatch.setenv("SHM_AUTH_TOKEN_STDIN", "1")
    with patch("sys.stdin", io.StringIO("not a token\n")), pytest.raises(RuntimeError, match="Invalid runtime"):
        _auth._get_id_token()
