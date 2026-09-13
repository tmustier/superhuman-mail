from __future__ import annotations

import io
import json
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


def test_api_headers_identify_shm_instead_of_using_urllib_default(monkeypatch):
    monkeypatch.delenv("SHM_AUTH_TOKEN_STDIN", raising=False)
    with (
        patch("superhuman_mail._auth._get_id_token", return_value="synthetic-runtime-token"),
        patch(
            "superhuman_mail._auth._config.load",
            return_value={"superhuman_api": {"device_id": "device-1", "version": "version-1"}},
        ),
    ):
        headers = _auth.api_headers()

    assert headers["User-Agent"] == "shm"
    assert headers["Authorization"] == "Bearer synthetic-runtime-token"


def test_desktop_token_exchange_identifies_shm_in_both_requests(monkeypatch):
    class Response:
        def __init__(self, payload, headers=None):
            self.payload = payload
            self.headers = headers or {}

        def read(self) -> bytes:
            return json.dumps(self.payload).encode()

    responses = [
        Response({"csrfToken": "csrf"}, {"Set-Cookie": "csrf=cookie; Secure"}),
        Response({"authData": {"idToken": "synthetic-id-token", "expiresIn": 3600}}),
    ]
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return responses.pop(0)

    _auth._token_cache.clear()
    monkeypatch.delenv("SHM_AUTH_TOKEN_STDIN", raising=False)
    with (
        patch("superhuman_mail._auth._config.api", side_effect=lambda key: {"google_id": "1111111111", "email": "one@example.com"}[key]),
        patch(
            "superhuman_mail._auth._config.load",
            return_value={"superhuman_api": {"device_id": "device-1", "version": "version-1"}},
        ),
        patch("superhuman_mail._auth._get_session_cookie", return_value="session"),
        patch("superhuman_mail._auth.urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        assert _auth._get_id_token() == "synthetic-id-token"

    assert len(requests) == 2
    assert all(request.get_header("User-agent") == "shm" for request in requests)
    _auth._token_cache.clear()
