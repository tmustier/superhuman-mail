"""Auth bootstrap for Superhuman private API.

Reads session cookies from the local Superhuman desktop app,
exchanges them for ID tokens via Superhuman auth endpoints,
and provides api_headers() for authenticated API calls.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
import urllib.request
from typing import Any

from . import _config

# ---------------------------------------------------------------------------
# Cookie decryption (reads Superhuman Electron app's encrypted cookie DB)
# ---------------------------------------------------------------------------

def _get_encryption_key() -> bytes:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", "Superhuman Safe Storage", "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not read Superhuman Safe Storage from Keychain")
    password = result.stdout.strip()
    return hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, dklen=16)


def _decrypt_cookie(enc_value: bytes, key: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if enc_value[:3] != b"v10":
        raise ValueError(f"Unknown cookie encryption version: {enc_value[:3]}")
    payload = enc_value[3:]
    cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16))
    dec = cipher.decryptor()
    pt = dec.update(payload) + dec.finalize()
    pad_len = pt[-1]
    if pad_len <= 16:
        pt = pt[:-pad_len]
    for offset in [0, 16, 32]:
        candidate = pt[offset:].decode("latin-1").rstrip("\x00")
        if candidate and all(c.isprintable() or c in "\r\n\t" for c in candidate[:20]):
            return candidate
    return pt.decode("latin-1")


def _get_session_cookie() -> str:
    key = _get_encryption_key()
    google_id = _config.api("google_id")
    cookie_db = _config.superhuman_base() / "Cookies"
    conn = sqlite3.connect(f"file:{cookie_db}?mode=ro&immutable=1", uri=True)
    try:
        row = conn.execute(
            "SELECT encrypted_value FROM cookies "
            "WHERE host_key='accounts.superhuman.com' AND name=?",
            (google_id,),
        ).fetchone()
        if not row:
            raise RuntimeError("Superhuman session cookie not found — is the app running?")
        return _decrypt_cookie(row[0], key)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Token exchange (CSRF → ID token)
# ---------------------------------------------------------------------------

_token_cache: dict[str, Any] = {}


def _get_id_token() -> str:
    """Exchange session cookie for a fresh ID token (cached until near-expiry)."""
    if "id_token" in _token_cache and "expires" in _token_cache:
        if time.time() < float(_token_cache["expires"]):
            return str(_token_cache["id_token"])

    google_id = _config.api("google_id")
    email = _config.api("email")
    cfg = _config.load()["superhuman_api"]
    device_id = cfg.get("device_id", "")
    version = cfg.get("version", "")
    session_cookie = _get_session_cookie()

    headers = {
        "Content-Type": "application/json",
        "Origin": "https://mail.superhuman.com",
        "x-device-id": device_id,
        "x-superhuman-version": version,
    }

    # Step 1: CSRF token
    req = urllib.request.Request(
        "https://accounts.superhuman.com/~backend/v3/sessions.getCsrfToken",
        headers={**headers, "Cookie": f"{google_id}={session_cookie}"},
    )
    resp = urllib.request.urlopen(req, timeout=15)

    csrf_cookie_val = None
    for h, v in resp.headers.items():
        if h.lower() == "set-cookie" and v.startswith("csrf="):
            csrf_cookie_val = v.split(";")[0].split("=", 1)[1]

    csrf_token = json.loads(resp.read())["csrfToken"]

    # Step 2: Exchange for ID token
    all_cookies = f"{google_id}={session_cookie}; csrf={csrf_cookie_val}"
    req2 = urllib.request.Request(
        "https://accounts.superhuman.com/~backend/v3/sessions.getTokens",
        data=json.dumps({"emailAddress": email, "googleId": google_id}).encode(),
        headers={**headers, "Cookie": all_cookies, "X-CSRF-Token": csrf_token},
        method="POST",
    )
    resp2 = urllib.request.urlopen(req2, timeout=15)
    data = json.loads(resp2.read())

    auth_data = data["authData"]
    id_token = auth_data["idToken"]
    expires_in = int(auth_data.get("expiresIn", 3600))

    _token_cache["id_token"] = id_token
    _token_cache["expires"] = time.time() + expires_in - 60

    return id_token


def api_headers() -> dict[str, str]:
    """Return headers for authenticated Superhuman API calls."""
    cfg = _config.load()["superhuman_api"]
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_get_id_token()}",
        "Origin": "https://mail.superhuman.com",
        "x-device-id": cfg.get("device_id", ""),
        "x-superhuman-version": cfg.get("version", ""),
    }


def check_auth() -> dict[str, str]:
    """Verify auth works end-to-end. Returns status info."""
    _get_id_token()
    remaining = float(_token_cache.get("expires", 0)) - time.time()
    return {"status": "ok", "token_expires_in_seconds": str(int(remaining))}
