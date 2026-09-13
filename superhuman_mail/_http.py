"""Shared HTTP request metadata for Superhuman API calls."""
from __future__ import annotations


# Superhuman's account service rejects urllib's default Python-urllib user
# agent. Identify the CLI explicitly rather than relying on a transport-library
# default.
USER_AGENT = "shm"


def client_headers() -> dict[str, str]:
    """Return headers that identify requests made by the shm client."""
    return {"User-Agent": USER_AGENT}
