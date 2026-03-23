"""Auto-bootstrap config.json by extracting credentials from the local Superhuman app.

Reads everything from the Superhuman desktop app's local data:
  - email        → config.json tab paths
  - google_id    → Cookie DB (numeric cookie name on accounts.superhuman.com)
  - device_id    → config.json → deviceId
  - team_id      → Local Storage LevelDB (team_XXXXX pattern)
  - shard_key    → derived from team_id
  - version      → Local Storage LevelDB (lastCodeVersion)
  - db_file      → first valid SQLite in File System/000/t/00/
  - author_name  → getTokens API response after bootstrap

Requires: Superhuman desktop app installed and signed in.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SH_BASE = Path(os.path.expanduser("~/Library/Application Support/Superhuman"))
_LEVELDB_DIR = _SH_BASE / "Local Storage" / "leveldb"
_COOKIE_DB = _SH_BASE / "Cookies"
_CONFIG_JSON = _SH_BASE / "config.json"
_FS_DIR = _SH_BASE / "File System" / "000" / "t" / "00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_app_installed() -> None:
    if not _SH_BASE.exists():
        raise RuntimeError(
            f"Superhuman data directory not found: {_SH_BASE}\n"
            "Is the Superhuman desktop app installed and signed in?"
        )


def _read_leveldb_strings(pattern: str) -> list[str]:
    """Scan LevelDB files for strings matching a regex pattern."""
    if not _LEVELDB_DIR.exists():
        return []
    hits: list[str] = []
    compiled = re.compile(pattern)
    for f in sorted(_LEVELDB_DIR.iterdir()):
        if f.suffix not in (".ldb", ".log"):
            continue
        data = f.read_bytes().decode("latin-1")
        for m in compiled.finditer(data):
            hits.append(m.group())
    return hits


# ---------------------------------------------------------------------------
# Extractors — each returns a single config value
# ---------------------------------------------------------------------------


def extract_email() -> str:
    """Extract email from Superhuman config.json tab paths."""
    if not _CONFIG_JSON.exists():
        raise RuntimeError(f"Superhuman config.json not found: {_CONFIG_JSON}")
    cfg = json.loads(_CONFIG_JSON.read_text())
    emails: set[str] = set()
    for window in cfg.get("state", {}).get("windows", []):
        for tab in window.get("tabs", []):
            path = tab.get("path", "")
            for part in path.strip("/").split("/"):
                if "@" in part and "." in part:
                    emails.add(part)
    if not emails:
        raise RuntimeError("Could not find email in Superhuman config.json tab paths")
    if len(emails) > 1:
        # Return all, caller can disambiguate
        return sorted(emails)[0]
    return emails.pop()


def extract_device_id() -> str:
    """Extract deviceId from Superhuman config.json."""
    if not _CONFIG_JSON.exists():
        raise RuntimeError(f"Superhuman config.json not found: {_CONFIG_JSON}")
    cfg = json.loads(_CONFIG_JSON.read_text())
    device_id = cfg.get("deviceId")
    if not device_id:
        raise RuntimeError("deviceId not found in Superhuman config.json")
    return str(device_id)


def extract_google_id() -> str:
    """Extract Google account ID from Superhuman cookie DB.

    The google_id is stored as a cookie NAME (not value) on accounts.superhuman.com.
    It's a long numeric string like '111089109521166025248'.
    """
    if not _COOKIE_DB.exists():
        raise RuntimeError(f"Superhuman cookie DB not found: {_COOKIE_DB}")
    conn = sqlite3.connect(f"file:{_COOKIE_DB}?mode=ro&immutable=1", uri=True)
    try:
        rows = conn.execute(
            "SELECT name FROM cookies WHERE host_key = 'accounts.superhuman.com'"
        ).fetchall()
        for (name,) in rows:
            if re.fullmatch(r"\d{10,}", name):
                return name
        raise RuntimeError(
            "Could not find Google account ID cookie in Superhuman cookie DB. "
            "Is the app signed in?"
        )
    finally:
        conn.close()


def extract_team_id() -> str:
    """Extract team ID from Local Storage LevelDB."""
    hits = _read_leveldb_strings(r"team_[A-Za-z0-9]{15,25}")
    if not hits:
        raise RuntimeError("Could not find team_id in Superhuman Local Storage")
    # Deduplicate and return the most common
    from collections import Counter
    return Counter(hits).most_common(1)[0][0]


def derive_shard_key(team_id: str) -> str:
    """Derive the 4-char shard key from a team_id.

    The shard key is characters 7-10 (0-indexed) of the part after the 'team_' prefix.
    Example: team_11UpmOv2bzCrYb7xqG → raw = '11UpmOv2bzCrYb7xqG' → shard = '2bzC'
    """
    raw = team_id.removeprefix("team_")
    if len(raw) < 11:
        raise RuntimeError(f"team_id too short to derive shard key: {team_id}")
    return raw[7:11]


_VERSION_TS_RE = re.compile(r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_ANCHORED_VERSION_RE = re.compile(
    r"lastCodeVersion.{0,32}?(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
)


def extract_version() -> str:
    """Extract the Superhuman version string from Local Storage LevelDB.

    Looks for the ``lastCodeVersion`` key and extracts the timestamp that
    follows it.  Falls back to the lexicographically latest UTC timestamp
    if the key isn't found (original behaviour).
    """
    if not _LEVELDB_DIR.exists():
        raise RuntimeError("Superhuman Local Storage not found")

    anchored: list[str] = []
    unanchored: list[str] = []

    for f in sorted(_LEVELDB_DIR.iterdir()):
        if f.suffix not in (".ldb", ".log"):
            continue
        data = f.read_bytes().decode("latin-1")
        for m in _ANCHORED_VERSION_RE.finditer(data):
            anchored.append(m.group(1))
        for m in _VERSION_TS_RE.finditer(data):
            unanchored.append(m.group())

    if anchored:
        return sorted(set(anchored))[-1]
    if unanchored:
        return sorted(set(unanchored))[-1]
    raise RuntimeError("Could not find version timestamp in Superhuman Local Storage")


def extract_db_file() -> str:
    """Find the active SQLite DB file in Superhuman's File System directory.

    The DB files have a 4096-byte header followed by SQLite content.
    """
    if not _FS_DIR.exists():
        raise RuntimeError(f"Superhuman File System directory not found: {_FS_DIR}")
    for entry in sorted(_FS_DIR.iterdir()):
        if not entry.is_file():
            continue
        with open(entry, "rb") as f:
            f.seek(4096)
            header = f.read(6)
        if header == b"SQLite":
            return entry.name
    raise RuntimeError("No valid SQLite DB file found in Superhuman File System")


def extract_author_name(email: str, google_id: str, device_id: str, version: str) -> str:
    """Extract author name via Google userinfo (using the Superhuman access token).

    Falls back to deriving from email if the API call fails.
    """
    try:
        key = _get_encryption_key()
        session_cookie = _decrypt_session_cookie(google_id, key)

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
        csrf_cookie = None
        for h, v in resp.headers.items():
            if h.lower() == "set-cookie" and v.startswith("csrf="):
                csrf_cookie = v.split(";")[0].split("=", 1)[1]
        csrf_token = json.loads(resp.read())["csrfToken"]

        # Step 2: Exchange for tokens
        all_cookies = f"{google_id}={session_cookie}; csrf={csrf_cookie}"
        req2 = urllib.request.Request(
            "https://accounts.superhuman.com/~backend/v3/sessions.getTokens",
            data=json.dumps({"emailAddress": email, "googleId": google_id}).encode(),
            headers={**headers, "Cookie": all_cookies, "X-CSRF-Token": csrf_token},
            method="POST",
        )
        resp2 = urllib.request.urlopen(req2, timeout=15)
        data = json.loads(resp2.read())
        access_token = data.get("authData", {}).get("accessToken", "")

        # Step 3: Google userinfo (returns full name)
        if access_token:
            req3 = urllib.request.Request(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp3 = urllib.request.urlopen(req3, timeout=15)
            userinfo = json.loads(resp3.read())
            name = userinfo.get("name", "")
            if name:
                return name
    except Exception:
        pass

    # Fallback: derive from email local part
    local = email.split("@")[0]
    parts = re.split(r"[._-]", local)
    return " ".join(p.capitalize() for p in parts)


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


def _decrypt_session_cookie(google_id: str, key: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    conn = sqlite3.connect(f"file:{_COOKIE_DB}?mode=ro&immutable=1", uri=True)
    try:
        row = conn.execute(
            "SELECT encrypted_value FROM cookies "
            "WHERE host_key='accounts.superhuman.com' AND name=?",
            (google_id,),
        ).fetchone()
        if not row:
            raise RuntimeError("Session cookie not found")
        enc = row[0]
        if enc[:3] != b"v10":
            raise ValueError(f"Unknown cookie encryption: {enc[:3]}")
        payload = enc[3:]
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
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main setup flow
# ---------------------------------------------------------------------------


def run_setup(config_path: Path | None = None) -> dict[str, Any]:
    """Extract all credentials and write config.json.

    Returns the generated config dict.
    """
    _check_app_installed()

    target = config_path or Path(__file__).resolve().parents[1] / "config.json"

    steps: list[dict[str, str]] = []
    errors: list[str] = []

    # 1. Email
    try:
        email = extract_email()
        steps.append({"field": "email", "status": "ok", "value": email})
    except Exception as e:
        errors.append(f"email: {e}")
        email = ""

    # 2. Device ID
    try:
        device_id = extract_device_id()
        steps.append({"field": "device_id", "status": "ok", "value": device_id})
    except Exception as e:
        errors.append(f"device_id: {e}")
        device_id = ""

    # 3. Google ID
    try:
        google_id = extract_google_id()
        steps.append({"field": "google_id", "status": "ok", "value": google_id})
    except Exception as e:
        errors.append(f"google_id: {e}")
        google_id = ""

    # 4. Team ID
    try:
        team_id = extract_team_id()
        steps.append({"field": "team_id", "status": "ok", "value": team_id})
    except Exception as e:
        errors.append(f"team_id: {e}")
        team_id = ""

    # 5. Shard key
    try:
        shard_key = derive_shard_key(team_id) if team_id else ""
        if shard_key:
            steps.append({"field": "team_shard_key", "status": "ok", "value": shard_key})
    except Exception as e:
        errors.append(f"team_shard_key: {e}")
        shard_key = ""

    # 6. Version
    try:
        version = extract_version()
        steps.append({"field": "version", "status": "ok", "value": version})
    except Exception as e:
        errors.append(f"version: {e}")
        version = ""

    # 7. DB file
    try:
        db_file = extract_db_file()
        steps.append({"field": "db_file", "status": "ok", "value": db_file})
    except Exception as e:
        errors.append(f"db_file: {e}")
        db_file = ""

    # 8. Author name (requires successful extraction of email, google_id, device_id, version)
    author_name = ""
    if email and google_id and device_id and version:
        try:
            author_name = extract_author_name(email, google_id, device_id, version)
            steps.append({"field": "author_name", "status": "ok", "value": author_name})
        except Exception as e:
            errors.append(f"author_name: {e}")
    elif not errors:
        errors.append("author_name: skipped (missing prerequisites)")

    if errors:
        raise RuntimeError(
            f"Setup failed — could not extract {len(errors)} field(s):\n"
            + "\n".join(f"  • {e}" for e in errors)
        )

    # Build config
    config: dict[str, Any] = {
        "email_account": email,
        "superhuman": {
            "superhuman_base": "~/Library/Application Support/Superhuman",
            "accounts": [{"email": email, "db_file": db_file}],
        },
        "superhuman_api": {
            "email": email,
            "author_name": author_name,
            "google_id": google_id,
            "device_id": device_id,
            "team_id": team_id,
            "team_shard_key": shard_key,
            "version": version,
        },
    }

    # Merge with existing config to preserve extra keys (e.g. custom settings)
    if target.exists():
        try:
            existing = json.loads(target.read_text())
            existing.update(config)
            config = existing
        except (json.JSONDecodeError, OSError):
            pass  # Corrupted or unreadable — overwrite

    # Write
    target.write_text(json.dumps(config, indent=2) + "\n")

    return {"config": config, "path": str(target), "steps": steps}
