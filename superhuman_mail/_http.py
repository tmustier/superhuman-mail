"""Shared HTTP request metadata for Superhuman API calls."""
from __future__ import annotations


# urllib's default Python-urllib user agent is rejected by Superhuman's account
# service before session-cookie authentication is evaluated. Identify the CLI
# explicitly rather than relying on a transport-library default.
USER_AGENT = "shm"


def client_headers() -> dict[str, str]:
    """Return headers that identify requests made by the shm client."""
    return {"User-Agent": USER_AGENT}
