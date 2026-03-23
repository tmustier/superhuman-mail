"""Consistent JSON output envelope for agent-friendly CLI.

Every command returns:
    {"status": "succeeded"|"failed", "command": "...", "data": ..., "errors": [], "warnings": []}

Errors are classified:
    {"class": "auth"|"network"|"not-found"|"input"|"conflict"|"rate-limit",
     "code": "...", "retryable": true|false, "hint": "..."}
"""
from __future__ import annotations

import json
import sys
import urllib.error
from typing import Any


def ok(command: str, data: Any, warnings: list[str] | None = None) -> dict[str, Any]:
    """Build a success envelope."""
    return {
        "status": "succeeded",
        "command": command,
        "data": data,
        "errors": [],
        "warnings": warnings or [],
    }


def fail(command: str, errors: list[dict[str, Any]], warnings: list[str] | None = None) -> dict[str, Any]:
    """Build a failure envelope."""
    return {
        "status": "failed",
        "command": command,
        "data": None,
        "errors": errors,
        "warnings": warnings or [],
    }


def error(cls: str, code: str, retryable: bool, hint: str) -> dict[str, Any]:
    """Build a single classified error."""
    return {"class": cls, "code": code, "retryable": retryable, "hint": hint}


def classify_exception(e: Exception) -> dict[str, Any]:
    """Classify an exception into a structured error dict."""
    if isinstance(e, urllib.error.HTTPError):
        status = e.code
        try:
            body = e.read().decode(errors="ignore")[:300]
        except Exception:
            body = ""
        if status == 401:
            return error("auth", "TOKEN_EXPIRED", True, "Restart Superhuman app or run `shm doctor`")
        if status == 403:
            return error("auth", "FORBIDDEN", False, "Check account permissions")
        if status == 404:
            return error("not-found", "NOT_FOUND", False, "Resource not found on server")
        if status == 409:
            return error("conflict", "CONFLICT", True, "Resource was modified concurrently — retry")
        if status == 429:
            return error("rate-limit", "RATE_LIMITED", True, "Rate limited — wait and retry")
        return error("network", f"HTTP_{status}", status >= 500, f"Server returned {status}: {body}")

    if isinstance(e, urllib.error.URLError):
        return error("network", "UNREACHABLE", True, f"Cannot reach server: {e.reason}")
    if isinstance(e, FileNotFoundError):
        return error("input", "FILE_NOT_FOUND", False, str(e))
    if isinstance(e, KeyError):
        return error("input", "MISSING_KEY", False, f"Missing key: {e}")
    if isinstance(e, ValueError):
        return error("input", "INVALID_VALUE", False, str(e))
    if isinstance(e, RuntimeError):
        return error("input", "RUNTIME_ERROR", False, str(e))
    return error("network", "UNKNOWN", False, f"{type(e).__name__}: {e}")


def emit(result: dict[str, Any], *, exit_code: int | None = None) -> None:
    """Print result as JSON and exit with appropriate code."""
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    sys.exit(exit_code if exit_code is not None else (0 if result["status"] == "succeeded" else 1))
