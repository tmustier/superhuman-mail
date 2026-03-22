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
            f"Copy config.example.json to config.json and fill in your values."
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
