"""Configuration loader for superhuman-mail."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_cache: dict[str, Any] | None = None


def _find_config() -> Path:
    """Resolve config.json path from env var or repo root."""
    raw = os.environ.get("SUPERHUMAN_MAIL_CONFIG") or os.environ.get("EMAIL_ACTIONS_CONFIG")
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[1] / "config.json"


def load() -> dict[str, Any]:
    """Load and cache config.json."""
    global _cache
    if _cache is not None:
        return _cache
    path = _find_config()
    if not path.exists():
        raise FileNotFoundError(
            f"Config not found: {path}. "
            f"Run `shm setup` to auto-generate config.json from your local Superhuman app."
        )
    _cache = json.loads(path.read_text())
    return _cache


def reset() -> None:
    """Clear config cache (for testing)."""
    global _cache
    _cache = None


def api(key: str) -> str:
    """Read a key from the superhuman_api config section."""
    return str(load()["superhuman_api"][key])


def email_account() -> str:
    """The primary email account."""
    return str(load()["email_account"])


def superhuman_base() -> Path:
    """Path to the Superhuman data directory."""
    return Path(os.path.expanduser(str(load()["superhuman"]["superhuman_base"])))


def accounts() -> list[dict[str, Any]]:
    """List of configured Superhuman accounts."""
    return list(load()["superhuman"]["accounts"])


def timezone() -> str:
    """User timezone as an IANA name. Reads from config, falls back to system."""
    tz = load().get("superhuman_api", {}).get("timezone")
    if tz:
        return str(tz)
    # Detect from /etc/localtime symlink (macOS/Linux)
    import os
    link = "/etc/localtime"
    if os.path.islink(link):
        target = os.path.realpath(link)
        for marker in ("/zoneinfo/", "/zone_info/"):
            idx = target.find(marker)
            if idx != -1:
                iana = target[idx + len(marker):]
                # Strip posix/ or right/ prefix if present
                for prefix in ("posix/", "right/"):
                    if iana.startswith(prefix):
                        iana = iana[len(prefix):]
                return iana
    return "UTC"
