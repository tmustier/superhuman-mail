"""Private local state paths and permissions."""
from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    raw = os.environ.get("SHM_STATE_DIR")
    path = Path(raw).expanduser() if raw else Path.home() / "Library" / "Application Support" / "superhuman-mail"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def private_file(path: Path) -> Path:
    """Best-effort enforce 0600 on an existing local state file."""
    if path.exists():
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path
