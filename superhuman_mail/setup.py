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
import tempfile
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


def _read_config_json() -> dict[str, Any]:
    if not _CONFIG_JSON.exists():
        raise RuntimeError(f"Superhuman config.json not found: {_CONFIG_JSON}")
    return json.loads(_CONFIG_JSON.read_text())


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


def extract_emails() -> list[str]:
    """Extract signed-in account emails from Superhuman config.json tab paths."""
    cfg = _read_config_json()
    emails: set[str] = set()
    for window in cfg.get("state", {}).get("windows", []):
        for tab in window.get("tabs", []):
            path = tab.get("path", "")
            for part in path.strip("/").split("/"):
                if "@" in part and "." in part:
                    emails.add(part)
    if not emails:
        raise RuntimeError("Could not find email in Superhuman config.json tab paths")
    return sorted(emails)


def extract_email(preferred: str | None = None) -> str:
    """Select the email account to bootstrap.

    When multiple signed-in accounts are detected, callers must provide
    ``preferred`` to avoid an arbitrary pick.
    """
    emails = extract_emails()
    if preferred:
        wanted = preferred.strip().lower()
        match = next((email for email in emails if email.lower() == wanted), None)
        if match:
            return match
        raise RuntimeError(
            f"Requested email not found in signed-in Superhuman accounts: {preferred}. "
            f"Available: {', '.join(emails)}"
        )
    if len(emails) > 1:
        raise RuntimeError(
            "Multiple Superhuman accounts detected. "
            "Re-run `shm setup --email <address>` to choose one. "
            f"Available: {', '.join(emails)}"
        )
    return emails[0]


def extract_device_id() -> str:
    """Extract deviceId from Superhuman config.json."""
    if not _CONFIG_JSON.exists():
        raise RuntimeError(f"Superhuman config.json not found: {_CONFIG_JSON}")
    cfg = json.loads(_CONFIG_JSON.read_text())
    device_id = cfg.get("deviceId")
    if not device_id:
        raise RuntimeError("deviceId not found in Superhuman config.json")
    return str(device_id)


def extract_google_ids() -> list[str]:
    """Extract all Google account ID cookie names from Superhuman's cookie DB."""
    if not _COOKIE_DB.exists():
        raise RuntimeError(f"Superhuman cookie DB not found: {_COOKIE_DB}")
    conn = sqlite3.connect(f"file:{_COOKIE_DB}?mode=ro&immutable=1", uri=True)
    try:
        rows = conn.execute(
            "SELECT name FROM cookies WHERE host_key = 'accounts.superhuman.com'"
        ).fetchall()
        ids = sorted({str(name) for (name,) in rows if re.fullmatch(r"\d{10,}", str(name or ""))})
        if ids:
            return ids
        raise RuntimeError(
            "Could not find Google account ID cookie in Superhuman cookie DB. "
            "Is the app signed in?"
        )
    finally:
        conn.close()


def extract_google_id(email: str, device_id: str, version: str) -> str:
    """Select the Google account ID that matches the chosen email account."""
    candidates = extract_google_ids()

    matches: list[str] = []
    for google_id in candidates:
        try:
            _request_auth_data(email, google_id, device_id, version)
            matches.append(google_id)
        except Exception:
            continue

    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(
            f"Multiple Google account cookies detected, but none matched {email}. "
            "Re-open Superhuman on the desired account and re-run setup."
        )
    raise RuntimeError(
        f"Multiple Google account cookies matched {email}: {', '.join(matches)}. "
        "Please sign out of the extra account(s) and re-run setup."
    )


def extract_team_ids() -> list[str]:
    """Extract distinct team IDs from Local Storage LevelDB."""
    hits = _read_leveldb_strings(r"team_[A-Za-z0-9]{15,25}")
    if not hits:
        raise RuntimeError("Could not find team_id in Superhuman Local Storage")
    return sorted(set(hits))


def extract_team_id() -> str:
    """Extract team ID from Local Storage LevelDB.

    We only accept a single distinct team id. If multiple are present,
    setup cannot safely map the selected mailbox to the right team.
    """
    team_ids = extract_team_ids()
    if len(team_ids) == 1:
        return team_ids[0]
    raise RuntimeError(
        "Multiple Superhuman team IDs detected. "
        "Setup cannot safely map the selected account to the right team. "
        f"Found: {', '.join(team_ids)}"
    )


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
    r"lastCodeVersion[\s\S]{0,32}?(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
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


def _db_owner_email(entry: Path) -> str | None:
    """Best-effort: read the mailbox owner email from a wrapped SQLite file."""
    fd, temp_path = tempfile.mkstemp(prefix="shm-setup-db-", suffix=".sqlite3")
    try:
        with open(entry, "rb") as src, os.fdopen(fd, "wb") as dst:
            src.seek(4096)
            dst.write(src.read())
        conn = sqlite3.connect(temp_path)
        try:
            row = conn.execute(
                "SELECT json FROM general WHERE key = 'teamMembers' LIMIT 1"
            ).fetchone()
            if not row or not row[0]:
                return None
            data = json.loads(row[0])
            email = data.get("user", {}).get("emailAddress")
            return str(email) if email else None
        finally:
            conn.close()
    except Exception:
        return None
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def extract_accounts() -> list[dict[str, str]]:
    """Discover configured Superhuman accounts from local wrapped SQLite DB files.

    If multiple DB files map to the same email, that account is omitted here
    because the mapping is ambiguous. The selected primary account is added
    separately by ``run_setup()`` after ``extract_db_file(email)`` resolves it.
    """
    if not _FS_DIR.exists():
        raise RuntimeError(f"Superhuman File System directory not found: {_FS_DIR}")

    matches: dict[str, list[dict[str, str]]] = {}
    for entry in sorted(_FS_DIR.iterdir()):
        if not entry.is_file():
            continue
        with open(entry, "rb") as f:
            f.seek(4096)
            header = f.read(6)
        if header != b"SQLite":
            continue
        email = _db_owner_email(entry)
        if not email:
            continue
        matches.setdefault(email.lower(), []).append({"email": email, "db_file": entry.name})

    accounts: list[dict[str, str]] = []
    for key in sorted(matches):
        entries = matches[key]
        if len(entries) == 1:
            accounts.append(entries[0])
    return accounts


def extract_db_file(email: str | None = None) -> str:
    """Find the active SQLite DB file in Superhuman's File System directory.

    The DB files have a 4096-byte header followed by SQLite content.
    When multiple DBs are present, callers must provide an email so the
    correct mailbox can be selected deterministically.
    """
    if not _FS_DIR.exists():
        raise RuntimeError(f"Superhuman File System directory not found: {_FS_DIR}")

    candidates: list[tuple[Path, str | None]] = []
    for entry in sorted(_FS_DIR.iterdir()):
        if not entry.is_file():
            continue
        with open(entry, "rb") as f:
            f.seek(4096)
            header = f.read(6)
        if header == b"SQLite":
            candidates.append((entry, _db_owner_email(entry)))

    if not candidates:
        raise RuntimeError("No valid SQLite DB file found in Superhuman File System")

    if len(candidates) == 1:
        entry, owner = candidates[0]
        if email and owner and owner.lower() != email.lower():
            raise RuntimeError(
                f"The only SQLite DB file found belongs to {owner}, not {email}: {entry.name}"
            )
        return entry.name

    if email:
        target = email.lower()
        matches = [entry.name for entry, owner in candidates if owner and owner.lower() == target]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple SQLite DB files matched {email}: {', '.join(matches)}"
            )
        details = [f"{entry.name} ({owner})" if owner else entry.name for entry, owner in candidates]
        raise RuntimeError(
            f"Multiple SQLite DB files detected, but none matched {email}. "
            f"Available: {', '.join(details)}"
        )

    details = [f"{entry.name} ({owner})" if owner else entry.name for entry, owner in candidates]
    raise RuntimeError(
        "Multiple SQLite DB files detected. "
        "Re-run `shm setup --email <address>` to choose the right mailbox. "
        f"Available: {', '.join(details)}"
    )


def _request_auth_data(email: str, google_id: str, device_id: str, version: str) -> dict[str, Any]:
    """Exchange a local Superhuman session cookie for auth tokens."""
    key = _get_encryption_key()
    session_cookie = _decrypt_session_cookie(google_id, key)

    headers = {
        "Content-Type": "application/json",
        "Origin": "https://mail.superhuman.com",
        "x-device-id": device_id,
        "x-superhuman-version": version,
    }

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

    all_cookies = f"{google_id}={session_cookie}; csrf={csrf_cookie}"
    req2 = urllib.request.Request(
        "https://accounts.superhuman.com/~backend/v3/sessions.getTokens",
        data=json.dumps({"emailAddress": email, "googleId": google_id}).encode(),
        headers={**headers, "Cookie": all_cookies, "X-CSRF-Token": csrf_token},
        method="POST",
    )
    resp2 = urllib.request.urlopen(req2, timeout=15)
    return json.loads(resp2.read())


def extract_author_name(email: str, google_id: str, device_id: str, version: str) -> str:
    """Extract author name via Google userinfo (using the Superhuman access token).

    Falls back to deriving from email if the API call fails.
    """
    try:
        data = _request_auth_data(email, google_id, device_id, version)
        access_token = data.get("authData", {}).get("accessToken", "")

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


def run_setup(config_path: Path | None = None, *, email: str | None = None) -> dict[str, Any]:
    """Extract all credentials and write config.json.

    Returns the generated config dict.
    """
    _check_app_installed()

    target = config_path or Path(__file__).resolve().parents[1] / "config.json"

    steps: list[dict[str, str]] = []
    errors: list[str] = []

    # 1. Email
    try:
        selected_email = extract_email(email)
        steps.append({"field": "email", "status": "ok", "value": selected_email})
    except Exception as e:
        errors.append(f"email: {e}")
        selected_email = ""

    # 2. Device ID
    try:
        device_id = extract_device_id()
        steps.append({"field": "device_id", "status": "ok", "value": device_id})
    except Exception as e:
        errors.append(f"device_id: {e}")
        device_id = ""

    # 3. Version
    try:
        version = extract_version()
        steps.append({"field": "version", "status": "ok", "value": version})
    except Exception as e:
        errors.append(f"version: {e}")
        version = ""

    # 4. Google ID
    try:
        google_id = extract_google_id(selected_email, device_id, version) if selected_email and device_id and version else ""
        if google_id:
            steps.append({"field": "google_id", "status": "ok", "value": google_id})
        elif not errors:
            errors.append("google_id: skipped (missing prerequisites)")
    except Exception as e:
        errors.append(f"google_id: {e}")
        google_id = ""

    # 5. Team ID
    try:
        team_id = extract_team_id()
        steps.append({"field": "team_id", "status": "ok", "value": team_id})
    except Exception as e:
        errors.append(f"team_id: {e}")
        team_id = ""

    # 6. Shard key
    try:
        shard_key = derive_shard_key(team_id) if team_id else ""
        if shard_key:
            steps.append({"field": "team_shard_key", "status": "ok", "value": shard_key})
    except Exception as e:
        errors.append(f"team_shard_key: {e}")
        shard_key = ""

    # 7. DB file
    try:
        db_file = extract_db_file(selected_email) if selected_email else ""
        if db_file:
            steps.append({"field": "db_file", "status": "ok", "value": db_file})
        elif not errors:
            errors.append("db_file: skipped (missing prerequisites)")
    except Exception as e:
        errors.append(f"db_file: {e}")
        db_file = ""

    # 8. Author name (requires successful extraction of email, google_id, device_id, version)
    author_name = ""
    if selected_email and google_id and device_id and version:
        try:
            author_name = extract_author_name(selected_email, google_id, device_id, version)
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

    accounts = extract_accounts()
    if not any(a["email"].lower() == selected_email.lower() for a in accounts):
        accounts.append({"email": selected_email, "db_file": db_file})
    accounts = sorted(accounts, key=lambda a: a["email"].lower())

    # Build config
    config: dict[str, Any] = {
        "email_account": selected_email,
        "superhuman": {
            "superhuman_base": "~/Library/Application Support/Superhuman",
            "accounts": accounts,
        },
        "superhuman_api": {
            "email": selected_email,
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
