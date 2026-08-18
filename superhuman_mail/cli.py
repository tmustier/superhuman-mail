"""CLI entry point for shm — Superhuman Mail agent-friendly CLI.

Usage:
    shm thread messages <thread_id>
    shm attachment download <thread_id> --output <directory>
    shm opens <thread_id>
    shm opens --recent
    shm draft reply <thread_id> --body "..."
    shm send --dry-run <thread_id> <draft_id>
    shm doctor
    shm schema [command]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import _auth, _config, _local
from . import approval as _approval
from . import attachment as _attachment
from . import attestation as _attestation
from . import authority_client as _authority_client
from . import comment as _comment
from . import draft as _draft
from . import executor as _executor
from . import opens as _opens
from . import reader as _reader
from . import send as _send
from . import setup as _setup
from . import share as _share
from . import thread as _thread
from ._envelope import emit, error, fail, ok

__version__ = "0.3.1"

_COMMANDS = ["reader", "thread", "attachment", "opens", "draft", "comment", "send", "attestation", "approval", "executor", "executor-contract", "setup", "doctor", "schema"]

# ---------------------------------------------------------------------------
# Schema definition (for agent introspection)
# ---------------------------------------------------------------------------

SCHEMA: dict[str, dict[str, Any]] = {
    "reader.scan": {
        "description": "Bounded read-only scan of transient local-cache snapshots",
        "contract_version": "1.0",
        "args": {
            "--since": {"required": True, "type": "UTC-Z timestamp", "semantics": "inclusive"},
            "--before": {"required": True, "type": "UTC-Z timestamp", "semantics": "exclusive"},
            "--account": {"required": False, "type": "string[]", "repeatable": True, "default": "all configured accounts", "semantics": "exact"},
            "--projection": {"required": False, "type": "metadata|full", "default": "metadata"},
            "--thread": {"required": False, "type": "string[]", "repeatable": True, "semantics": "exact OR"},
            "--person": {"required": False, "type": "email[]", "repeatable": True, "semantics": "normalized exact OR across from/to/cc/bcc"},
        },
        "selector_semantics": "OR within thread/person categories; AND across categories and the time window",
        "coverage": "One deterministic bounded observed set; no provider cursor. Truncation is explicit.",
        "limits": _reader.contract_limits(),
        "safety": "read",
        "examples": [
            "shm reader scan --since 2026-01-01T00:00:00Z --before 2026-01-02T00:00:00Z",
            "shm reader scan --since 2026-01-01T00:00:00Z --before 2026-01-02T00:00:00Z --account owner@example.com --projection full --person sender@example.com",
        ],
    },
    "thread.messages": {
        "description": "Read thread messages from local Superhuman DB",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--account": {"required": False, "type": "string", "hint": "Email account to use (multi-account)"},
        },
        "safety": "read",
        "examples": [
            "shm thread messages 19d001f35612a211",
            "shm thread messages 19d001f35612a211 --account owner@example.com",
        ],
    },
    "attachment.download": {
        "description": "Download received attachment bytes through Superhuman's authenticated media service",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--output": {"required": True, "type": "directory", "hint": "Created with private permissions when absent"},
            "--account": {"required": False, "type": "string", "hint": "Email account to use (multi-account)"},
            "--message-id": {"required": False, "type": "string", "hint": "Download attachments from one exact message"},
            "--attachment-id": {"required": False, "type": "string", "hint": "Download one exact provider attachment"},
        },
        "limits": {
            "attachment_bytes": _attachment.MAX_ATTACHMENT_BYTES,
            "total_bytes": _attachment.MAX_TOTAL_BYTES,
        },
        "coverage": "Requires message metadata in Superhuman's local sync cache; attachment bytes need not be cached",
        "safety": "read",
        "examples": [
            "shm attachment download 19d001f35612a211 --output ./attachments",
            "shm attachment download 19d001f35612a211 --account owner@example.com --output ./attachments",
            "shm attachment download 19d001f35612a211 --attachment-id ATTACHMENT_ID --output ./attachments",
        ],
    },
    "thread.userdata": {
        "description": "Advanced: raw thread userdata dump. Prefer draft read, comment read, or opens for specific data.",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--account": {"required": False, "type": "string"},
        },
        "safety": "read",
        "examples": [
            "shm thread userdata 19d001f35612a211",
        ],
    },
    "thread.list": {
        "description": "List recent threads from local DB, sorted by recency",
        "args": {
            "--limit": {"required": False, "type": "int", "default": 20},
            "--unread": {"required": False, "type": "flag"},
            "--participants": {"required": False, "type": "flag", "hint": "Include full participant list"},
            "--fail-empty": {"required": False, "type": "flag", "hint": "Exit code 3 if no results"},
            "--account": {"required": False, "type": "string", "hint": "Email account to use (multi-account)"},
        },
        "safety": "read",
        "examples": [
            "shm thread list --limit 10",
            "shm thread list --unread",
            "shm thread list --unread --participants --limit 5",
        ],
    },
    "thread.search": {
        "description": "Search threads using the local FTS index, sorted by recency",
        "args": {
            "query": {"required": True, "type": "string"},
            "--limit": {"required": False, "type": "int", "default": 10},
            "--unread": {"required": False, "type": "flag"},
            "--participants": {"required": False, "type": "flag", "hint": "Include full participant list"},
            "--fail-empty": {"required": False, "type": "flag", "hint": "Exit code 3 if no results"},
            "--account": {"required": False, "type": "string", "hint": "Email account to use (multi-account)"},
        },
        "safety": "read",
        "examples": [
            "shm thread search \"kalgin follow up\"",
            "shm thread search \"invoice\" --limit 5 --unread",
            "shm thread search \"proposal\" --participants --fail-empty",
        ],
    },
    "opens": {
        "description": "Read per-message read statuses / read receipts from API",
        "args": {
            "thread_id": {"required": False, "type": "string"},
            "--recent": {"required": False, "type": "flag", "hint": "Show recent opens across threads"},
            "--recipient": {"required": False, "type": "string", "hint": "Filter to a specific recipient email"},
            "--limit": {"required": False, "type": "int", "default": 20, "hint": "Max results for --recent mode"},
        },
        "safety": "read",
        "examples": [
            "shm opens 19d001f35612a211",
            "shm opens 19d001f35612a211 --recipient someone@example.com",
        ],
    },
    "opens.recent": {
        "description": "Read recent opens across threads from the local activity_feed table",
        "args": {
            "--recent": {"required": True, "type": "flag"},
            "--recipient": {"required": False, "type": "string", "hint": "Filter to a specific recipient email"},
            "--limit": {"required": False, "type": "int", "default": 20},
        },
        "safety": "read",
        "examples": [
            "shm opens --recent --limit 10",
            "shm opens --recent --recipient someone@example.com",
        ],
    },
    "draft.reply": {
        "description": "Create a reply draft on an existing thread",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--body": {"required": False, "type": "string", "hint": "Body text (required unless --body-file given; use '-' for stdin)"},
            "--body-file": {"required": False, "type": "filepath", "hint": "Read body from file (use instead of --body)"},
            "--body-html": {"required": False, "type": "string"},
            "--body-html-file": {"required": False, "type": "filepath", "hint": "Read HTML body from file"},
            "--scheduled-for": {"required": False, "type": "string", "hint": "ISO datetime"},
            "--abort-on-reply": {"required": False, "type": "flag", "hint": "Cancel send if someone replies first"},
            "--reminder": {"required": False, "type": "string", "hint": "Follow-up reminder (ISO datetime)"},
            "--sensitivity-label-id": {"required": False, "type": "string"},
            "--sensitivity-tenant-id": {"required": False, "type": "string"},
        },
        "safety": "write",
        "examples": [
            "shm draft reply 19d001f35612a211 --body 'Thanks for the update'",
            "shm draft reply 19d001f35612a211 --body-file ./reply.txt",
            "shm draft reply 19d001f35612a211 --body 'See you then' --scheduled-for '2026-03-26T09:00:00Z'",
        ],
    },
    "draft.reply-all": {
        "description": "Create a reply-all draft on an existing thread",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--body": {"required": False, "type": "string", "hint": "Body text (required unless --body-file given; use '-' for stdin)"},
            "--body-file": {"required": False, "type": "filepath", "hint": "Read body from file (use instead of --body)"},
            "--body-html": {"required": False, "type": "string"},
            "--body-html-file": {"required": False, "type": "filepath", "hint": "Read HTML body from file"},
            "--scheduled-for": {"required": False, "type": "string"},
            "--abort-on-reply": {"required": False, "type": "flag", "hint": "Cancel send if someone replies first"},
            "--reminder": {"required": False, "type": "string", "hint": "Follow-up reminder (ISO datetime)"},
            "--sensitivity-label-id": {"required": False, "type": "string"},
            "--sensitivity-tenant-id": {"required": False, "type": "string"},
        },
        "safety": "write",
        "examples": [
            "shm draft reply-all 19d001f35612a211 --body 'Sounds good to everyone'",
            "shm draft reply-all 19d001f35612a211 --body-file ./reply.txt",
        ],
    },
    "draft.forward": {
        "description": "Create a forward draft on an existing thread",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--body": {"required": False, "type": "string", "hint": "Body text (required unless --body-file given; use '-' for stdin)"},
            "--body-file": {"required": False, "type": "filepath", "hint": "Read body from file (use instead of --body)"},
            "--to": {"required": False, "type": "string[]", "hint": "Repeatable"},
            "--cc": {"required": False, "type": "string[]"},
            "--bcc": {"required": False, "type": "string[]"},
            "--body-html": {"required": False, "type": "string"},
            "--body-html-file": {"required": False, "type": "filepath", "hint": "Read HTML body from file"},
            "--scheduled-for": {"required": False, "type": "string"},
            "--abort-on-reply": {"required": False, "type": "flag", "hint": "Cancel send if someone replies first"},
            "--reminder": {"required": False, "type": "string", "hint": "Follow-up reminder (ISO datetime)"},
            "--sensitivity-label-id": {"required": False, "type": "string"},
            "--sensitivity-tenant-id": {"required": False, "type": "string"},
        },
        "safety": "write",
        "examples": [
            "shm draft forward 19d001f35612a211 --body 'FYI — see below' --to someone@example.com",
            "shm draft forward 19d001f35612a211 --body-file ./fwd.txt --to a@example.com --cc b@example.com",
        ],
    },
    "draft.compose": {
        "description": "Create a new compose draft (new thread)",
        "args": {
            "--subject": {"required": True, "type": "string"},
            "--body": {"required": False, "type": "string", "hint": "Body text (required unless --body-file given; use '-' for stdin)"},
            "--body-file": {"required": False, "type": "filepath", "hint": "Read body from file (use instead of --body)"},
            "--to": {"required": False, "type": "string[]", "hint": "Repeatable"},
            "--cc": {"required": False, "type": "string[]"},
            "--bcc": {"required": False, "type": "string[]"},
            "--body-html": {"required": False, "type": "string"},
            "--body-html-file": {"required": False, "type": "filepath", "hint": "Read HTML body from file"},
            "--scheduled-for": {"required": False, "type": "string"},
            "--abort-on-reply": {"required": False, "type": "flag", "hint": "Cancel send if someone replies first"},
            "--reminder": {"required": False, "type": "string", "hint": "Follow-up reminder (ISO datetime)"},
            "--sensitivity-label-id": {"required": False, "type": "string"},
            "--sensitivity-tenant-id": {"required": False, "type": "string"},
        },
        "safety": "write",
        "examples": [
            "shm draft compose --subject 'Hello' --body 'Hi there' --to someone@example.com",
            "shm draft compose --subject 'Report' --body-file ./email.txt --to someone@example.com",
            "echo 'body' | shm draft compose --subject 'Hello' --body - --to someone@example.com",
        ],
    },
    "draft.read": {
        "description": "Read source drafts with canonical lifecycle and active/terminal counts",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--draft-id": {"required": False, "type": "string"},
            "--account": {"required": False, "type": "string"},
            "--active-only": {"required": False, "type": "flag"},
        },
        "safety": "read",
        "examples": [
            "shm draft read 19d001f35612a211 --active-only",
            "shm draft read 19d001f35612a211 --draft-id draft00abc123",
        ],
    },
    "draft.status": {
        "description": "Read canonical per-draft lifecycle/provenance",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--draft-id": {"required": False, "type": "string"},
            "--account": {"required": False, "type": "string"},
        },
        "safety": "read",
        "examples": ["shm draft status 19d001f35612a211 --draft-id draft00abc123 --account owner@example.com"],
    },
    "draft.attest-render": {
        "description": "Create a signed exact live-Superhuman render attestation without mail writes",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
            "--account": {"required": True, "type": "string"},
            "--output": {"required": True, "type": "directory"},
            "--cdp-url": {"required": False, "type": "string", "default": "http://127.0.0.1:9222"},
            "--window-id": {"required": False, "type": "int", "hint": "macOS window ID fallback for screenshots"},
            "--delay": {"required": False, "type": "int", "default": 20},
        },
        "safety": "read",
        "examples": ["shm draft attest-render THREAD DRAFT --account owner@example.com --output ./preview"],
    },
    "draft.prepare": {
        "description": "Trusted bridge: create the pre-approval render bundle",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
            "--account": {"required": True, "type": "string"},
            "--delay": {"required": True, "type": "int"},
        },
        "safety": "executor-only-read",
        "examples": [],
    },
    "draft.get": {
        "description": "Trusted bridge: rerender a draft and return its content-free executor binding",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
            "--account": {"required": True, "type": "string"},
            "--attestation": {"required": True, "type": "string"},
            "--cdp-url": {"required": False, "type": "string"},
            "--window-id": {"required": False, "type": "int"},
        },
        "safety": "executor-only-read",
        "examples": ["shm draft get THREAD DRAFT --account EMAIL --attestation ID"],
    },
    "draft.send": {
        "description": "Trusted bridge: conditional exact send after executor receipt claim",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
            "--account": {"required": True, "type": "string"},
            "--attestation": {"required": True, "type": "string"},
            "--if-revision": {"required": True, "type": "string"},
            "--expected-draft-fingerprint": {"required": True, "type": "string"},
            "--delay": {"required": True, "type": "int"},
            "--wait": {"required": False, "type": "float", "default": 120},
        },
        "safety": "executor-only-irreversible",
        "examples": ["shm draft send THREAD DRAFT --account EMAIL --attestation ID --if-revision sha256:... --expected-draft-fingerprint sha256:... --delay 20"],
    },
    "draft.discard": {
        "description": "Discard (soft-delete) a draft",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
        },
        "safety": "write",
        "examples": [
            "shm draft discard 19d001f35612a211 draft00abc123",
        ],
    },
    "draft.attach": {
        "description": "Upload a file and attach it to a draft",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
            "file": {"required": True, "type": "filepath"},
            "--content-type": {"required": False, "type": "string", "default": "application/octet-stream"},
        },
        "safety": "write",
        "examples": [
            "shm draft attach 19d001f35612a211 draft00abc123 ./report.pdf",
            "shm draft attach 19d001f35612a211 draft00abc123 ./image.png --content-type image/png",
        ],
    },
    "draft.share": {
        "description": "Share a draft with a collaboration link",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
            "--name": {"required": False, "type": "string"},
        },
        "safety": "write",
        "examples": [
            "shm draft share 19d001f35612a211 draft00abc123",
            "shm draft share 19d001f35612a211 draft00abc123 --name 'Q1 proposal'",
        ],
    },
    "draft.unshare": {
        "description": "Remove sharing from a draft",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
        },
        "safety": "write",
        "examples": [
            "shm draft unshare 19d001f35612a211 draft00abc123",
        ],
    },
    "comment.post": {
        "description": "Post a comment on a thread",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--body": {"required": True, "type": "string"},
            "--mention": {"required": False, "type": "pair[]", "hint": "EMAIL NAME, repeatable"},
        },
        "safety": "write",
        "examples": [
            "shm comment post 19d001f35612a211 --body 'Please review'",
            "shm comment post 19d001f35612a211 --body 'Thoughts?' --mention alice@co.com Alice",
        ],
    },
    "comment.read": {
        "description": "Read all comments on a thread",
        "args": {"thread_id": {"required": True, "type": "string"}},
        "safety": "read",
        "examples": [
            "shm comment read 19d001f35612a211",
        ],
    },
    "comment.read-many": {
        "description": "Read comments for many threads in batched API requests; fails on partial reads",
        "args": {
            "thread_ids": {"required": True, "type": "string[]"},
            "--batch-size": {"required": False, "type": "int", "default": 2},
        },
        "safety": "read",
        "examples": [
            "shm comment read-many THREAD_1 THREAD_2",
        ],
    },
    "comment.discard": {
        "description": "Delete a comment from a thread",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "comment_id": {"required": True, "type": "string"},
        },
        "safety": "write",
        "examples": [
            "shm comment discard 19d001f35612a211 cmt_1abc123",
        ],
    },
    "send": {
        "description": "Validate, reconcile, explicitly approve, or policy-send a qualified website inbound",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
            "--dry-run": {"required": False, "type": "flag", "hint": "Metadata/lifecycle preflight only"},
            "--status": {"required": False, "type": "flag", "hint": "Reconcile without sending"},
            "--confirm": {"required": False, "type": "flag", "hint": "Explicit exact-attested send"},
            "--qualified-website-inbound": {"required": False, "type": "flag", "hint": "Narrow unattended policy for a qualified website-inbound compose"},
            "--account": {"required": False, "type": "string"},
            "--lead-email": {"required": False, "type": "string", "hint": "Exact qualified website lead; required with --qualified-website-inbound"},
            "--qualification-ref": {"required": False, "type": "string", "hint": "website-inbounds:webin-<8 lowercase hex> source reference"},
            "--attestation": {"required": False, "type": "string", "hint": "Optional receipt-bound attestation ID consistency check"},
            "--approval-receipt": {"required": False, "type": "string", "hint": "Externally signed exact-send receipt; required with --confirm"},
            "--approval-ref": {"required": False, "type": "string", "hint": "Deprecated audit correlation; never authorizes"},
            "--cdp-url": {"required": False, "type": "string", "default": "http://127.0.0.1:9222"},
            "--window-id": {"required": False, "type": "int"},
            "--delay": {"required": False, "type": "int", "default": 20},
            "--wait": {"required": False, "type": "float", "default": 120},
        },
        "safety": "irreversible",
        "examples": [
            "shm send --dry-run THREAD DRAFT --account owner@example.com",
            "shm send status THREAD DRAFT --account owner@example.com --wait 120",
            "shm send --confirm THREAD DRAFT --account owner@example.com --approval-receipt RECEIPT.json --wait 120",
            "shm send --qualified-website-inbound THREAD DRAFT --account owner@example.com --lead-email lead@example.com --qualification-ref website-inbounds:webin-0123abcd",
        ],
    },
    "executor.status": {
        "description": "Read canonical authority state by receipt ID",
        "args": {"receipt_id": {"required": True, "type": "string"}},
        "safety": "read",
        "examples": ["shm executor status sha256:..."],
    },
    "executor.abort": {
        "description": "Abort a canonical authority execution while it is in grace",
        "args": {"receipt_id": {"required": True, "type": "string"}},
        "safety": "write",
        "examples": ["shm executor abort sha256:..."],
    },
    "executor-contract": {
        "description": "Report the credential-free trusted executor provider contract",
        "args": {},
        "safety": "read",
        "examples": ["shm executor-contract"],
    },
    "approval.verify": {
        "description": "Verify an externally signed exact-send receipt; consumption state lives in the executor",
        "args": {
            "reference": {"required": True, "type": "string"},
            "--attestation": {"required": True, "type": "string"},
        },
        "safety": "read",
        "examples": ["shm approval verify RECEIPT.json --attestation ID_OR_PATH"],
    },
    "attestation.show": {
        "description": "Verify and inspect a render attestation without exposing mail content",
        "args": {
            "reference": {"required": True, "type": "string", "hint": "Attestation ID or local artifact path"},
            "--account": {"required": False, "type": "string"},
            "--thread-id": {"required": False, "type": "string"},
            "--draft-id": {"required": False, "type": "string"},
        },
        "safety": "read",
        "examples": ["shm attestation show ID --account owner@example.com --thread-id THREAD --draft-id DRAFT"],
    },
    "setup": {
        "description": "Auto-detect credentials from local Superhuman app and write config.json",
        "args": {
            "--config": {"required": False, "type": "filepath", "hint": "Output path (default: config.json in repo root)"},
            "--email": {"required": False, "type": "string", "hint": "Choose account when multiple Superhuman accounts are signed in"},
        },
        "safety": "write",
        "examples": [
            "shm setup",
            "shm setup --email someone@example.com",
        ],
    },
    "doctor": {
        "description": "Verify config, auth, and connectivity",
        "args": {},
        "safety": "read",
        "examples": [
            "shm doctor",
        ],
    },
    "schema": {
        "description": "Introspect available commands",
        "args": {"command": {"required": False, "type": "string"}},
        "safety": "read",
        "examples": [
            "shm schema",
            "shm schema draft.reply",
        ],
    },
}

# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------


def _doctor() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    # 1. Config
    try:
        _config.load()
        checks.append({"name": "config", "status": "pass", "detail": f"Loaded from {_config._find_config()}"})
    except Exception as e:
        checks.append({"name": "config", "status": "fail", "detail": str(e)})
        return fail("doctor", [], warnings=[]) | {"data": {"checks": checks}}

    # 2. Superhuman data dir
    try:
        base = _config.superhuman_base()
        if base.exists():
            checks.append({"name": "superhuman_data", "status": "pass", "detail": str(base)})
        else:
            checks.append({"name": "superhuman_data", "status": "fail", "detail": f"Not found: {base}"})
    except Exception as e:
        checks.append({"name": "superhuman_data", "status": "fail", "detail": str(e)})

    # 3. Local DB
    try:
        db = _local.get_db_path()
        checks.append({"name": "local_db", "status": "pass", "detail": str(db)})
    except Exception as e:
        checks.append({"name": "local_db", "status": "fail", "detail": str(e)})

    # 4. Keychain
    try:
        _auth._get_encryption_key()
        checks.append({"name": "keychain", "status": "pass", "detail": "Superhuman Safe Storage accessible"})
    except Exception as e:
        checks.append({"name": "keychain", "status": "fail", "detail": str(e)})

    # 5. Auth token
    try:
        info = _auth.check_auth()
        checks.append({"name": "auth", "status": "pass", "detail": f"Token OK, expires in {info['token_expires_in_seconds']}s"})
    except Exception as e:
        checks.append({"name": "auth", "status": "fail", "detail": str(e)})

    all_pass = all(c["status"] == "pass" for c in checks)
    return ok("doctor", {"checks": checks, "all_pass": all_pass})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema_examples(key: str) -> list[str]:
    """Return the examples list for a SCHEMA key, falling back to empty."""
    entry = SCHEMA.get(key, {})
    return entry.get("examples", [])


def _examples_epilog(schema_key: str) -> str | None:
    """Build an argparse epilog string from SCHEMA examples."""
    examples = _schema_examples(schema_key)
    if not examples:
        return None
    lines = ["Examples:"] + [f"  {ex}" for ex in examples]
    return "\n".join(lines)


class _BodyValidationError(Exception):
    """Raised when --body/--body-file validation fails."""

    def __init__(self, hint: str) -> None:
        self.hint = hint


def _read_text_arg(value: str | None, file_path: str | None) -> str | None:
    """Read a text value from a direct arg, a file path, or stdin ('-').

    Raises OSError on file read failures (caller should catch).
    """
    if value == "-":
        return sys.stdin.read()
    if value is not None:
        return value
    if file_path:
        return Path(file_path).read_text()
    return None


def _validate_body(args: argparse.Namespace, command_label: str, schema_key: str) -> tuple[str, str | None]:
    """Validate and resolve body + body_html.  Raises _BodyValidationError."""
    body_val: str | None = getattr(args, "body", None)
    body_file_val: str | None = getattr(args, "body_file", None)
    html_val: str | None = getattr(args, "body_html", None)
    html_file_val: str | None = getattr(args, "body_html_file", None)

    examples = _schema_examples(schema_key)
    example_hint = f"\n  Example: {examples[0]}" if examples else ""

    # exactly-one-of --body / --body-file
    if body_val is not None and body_file_val is not None:
        raise _BodyValidationError(f"Provide --body or --body-file, not both.{example_hint}")
    if body_val is None and body_file_val is None:
        raise _BodyValidationError(f"--body or --body-file is required.{example_hint}")

    # at-most-one-of --body-html / --body-html-file
    if html_val is not None and html_file_val is not None:
        raise _BodyValidationError(f"Provide --body-html or --body-html-file, not both.{example_hint}")

    try:
        body = _read_text_arg(body_val, body_file_val)
    except OSError as e:
        raise _BodyValidationError(f"Cannot read body file: {e}{example_hint}") from e
    if body is None:
        raise _BodyValidationError(f"Could not read body.{example_hint}")

    try:
        body_html = _read_text_arg(html_val, html_file_val)
    except OSError as e:
        raise _BodyValidationError(f"Cannot read HTML body file: {e}{example_hint}") from e

    return body, body_html


# ---------------------------------------------------------------------------
# Custom ArgumentParser — JSON envelope on all errors
# ---------------------------------------------------------------------------


class _ShmParser(argparse.ArgumentParser):
    """ArgumentParser that emits JSON envelope errors instead of stderr text."""

    # Store the SCHEMA key so we can include examples in error output.
    _schema_key: str = ""

    def error(self, message: str) -> None:  # type: ignore[override]
        examples = _schema_examples(self._schema_key) if self._schema_key else []
        hint = message
        if examples:
            hint += "\n  Example: " + examples[0]
        envelope = fail(
            self._schema_key or "shm",
            [error("input", "INVALID_ARGS", False, hint)],
        )
        json.dump(envelope, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        raise SystemExit(1)

    def exit(self, status: int = 0, message: str | None = None) -> None:  # type: ignore[override]
        # Let --help and --version go through normally (status 0).
        raise SystemExit(status)


def _sub(parent_sub, name: str, *, help: str, schema_key: str = "", **kwargs: Any) -> _ShmParser:
    """Add a subparser that uses _ShmParser and wires up epilog + formatter."""
    epilog = _examples_epilog(schema_key) if schema_key else None
    sp: _ShmParser = parent_sub.add_parser(
        name,
        help=help,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        **kwargs,
    )
    sp._schema_key = schema_key  # noqa: SLF001
    return sp


# ---------------------------------------------------------------------------
# Argparse setup
# ---------------------------------------------------------------------------


def _build_parser() -> _ShmParser:
    p = _ShmParser(
        prog="shm",
        description="Superhuman Mail — agent-friendly CLI for the unofficial Superhuman API",
    )
    p.add_argument("--version", action="version", version=f"shm {__version__}")

    sub = p.add_subparsers(dest="command")

    # -- reader (production read-only contract) --
    reader_p = _sub(sub, "reader", help="Bounded production local-cache reader")
    rsub = reader_p.add_subparsers(dest="action")
    r_scan = _sub(rsub, "scan", help="Scan exact accounts and UTC window", schema_key="reader.scan")
    r_scan.add_argument("--since", required=True, help="Inclusive exact UTC-Z timestamp")
    r_scan.add_argument("--before", required=True, help="Exclusive exact UTC-Z timestamp")
    r_scan.add_argument("--account", action="append", default=[], help="Exact configured account; repeatable (default: all)")
    r_scan.add_argument("--projection", choices=("metadata", "full"), default="metadata")
    r_scan.add_argument("--thread", action="append", default=[], help="Exact thread ID; repeatable")
    r_scan.add_argument("--person", action="append", default=[], help="Exact normalized email; repeatable")

    # -- thread --
    thread_p = _sub(sub, "thread", help="Thread operations")
    tsub = thread_p.add_subparsers(dest="action")

    t_messages = _sub(tsub, "messages", help="Read messages from local DB", schema_key="thread.messages")
    t_messages.add_argument("thread_id")
    t_messages.add_argument("--account")

    t_ud = _sub(tsub, "userdata", help="Read userdata from API (advanced)", schema_key="thread.userdata")
    t_ud.add_argument("thread_id")
    t_ud.add_argument("--account")

    t_list = _sub(tsub, "list", help="List recent threads", schema_key="thread.list")
    t_list.add_argument("--limit", type=int, default=20)
    t_list.add_argument("--unread", action="store_true", help="Only unread threads")
    t_list.add_argument("--participants", action="store_true", help="Include full participant list")
    t_list.add_argument("--fail-empty", action="store_true", help="Exit code 3 if no results")
    t_list.add_argument("--account")

    t_search = _sub(tsub, "search", help="Search threads", schema_key="thread.search")
    t_search.add_argument("query")
    t_search.add_argument("--limit", type=int, default=10)
    t_search.add_argument("--unread", action="store_true", help="Only unread threads")
    t_search.add_argument("--participants", action="store_true", help="Include full participant list")
    t_search.add_argument("--fail-empty", action="store_true", help="Exit code 3 if no results")
    t_search.add_argument("--account")

    # -- attachment --
    attachment_p = _sub(sub, "attachment", help="Received attachment operations")
    atsub = attachment_p.add_subparsers(dest="action")
    at_download = _sub(
        atsub,
        "download",
        help="Download received attachments",
        schema_key="attachment.download",
        description=(
            "Download received attachment bytes. Requires the message metadata to be "
            "present in Superhuman's local sync cache; the attachment bytes themselves "
            "do not need to be cached or previously opened."
        ),
    )
    at_download.add_argument("thread_id", help="Thread ID present in the local sync cache")
    at_download.add_argument("--output", required=True, help="Destination directory")
    at_download.add_argument("--account")
    at_download.add_argument("--message-id")
    at_download.add_argument("--attachment-id")

    # -- opens --
    opens_p = _sub(sub, "opens", help="Read read receipts / opens for a thread or recent activity", schema_key="opens")
    opens_p.add_argument("thread_id", nargs="?", default=None)
    opens_p.add_argument("--recent", action="store_true", help="Show recent opens across threads")
    opens_p.add_argument("--recipient", help="Filter to a specific recipient email")
    opens_p.add_argument("--limit", type=int, default=20, help="Max results for --recent mode")

    # -- draft --
    draft_p = _sub(sub, "draft", help="Draft operations")
    dsub = draft_p.add_subparsers(dest="action")

    def _add_smart_send_args(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--scheduled-for", help="ISO datetime for scheduled send")
        parser.add_argument("--abort-on-reply", action="store_true", help="Cancel send if someone replies first")
        parser.add_argument("--reminder", help="Follow-up reminder (ISO datetime)")
        parser.add_argument("--sensitivity-label-id", help="Sensitivity label ID")
        parser.add_argument("--sensitivity-tenant-id", help="Sensitivity tenant ID")

    def _add_body_args(parser: argparse.ArgumentParser) -> None:
        """Add --body, --body-file, --body-html, --body-html-file (all optional at argparse level)."""
        parser.add_argument("--body", help="Message body text (use '-' to read from stdin)")
        parser.add_argument("--body-file", help="Read body from file path")
        parser.add_argument("--body-html", help="HTML body (use '-' to read from stdin)")
        parser.add_argument("--body-html-file", help="Read HTML body from file path")

    d_reply = _sub(dsub, "reply", help="Create reply draft", schema_key="draft.reply")
    d_reply.add_argument("thread_id")
    _add_body_args(d_reply)
    _add_smart_send_args(d_reply)

    d_ra = _sub(dsub, "reply-all", help="Create reply-all draft", schema_key="draft.reply-all")
    d_ra.add_argument("thread_id")
    _add_body_args(d_ra)
    _add_smart_send_args(d_ra)

    d_fwd = _sub(dsub, "forward", help="Create forward draft", schema_key="draft.forward")
    d_fwd.add_argument("thread_id")
    _add_body_args(d_fwd)
    d_fwd.add_argument("--to", action="append", default=[])
    d_fwd.add_argument("--cc", action="append", default=[])
    d_fwd.add_argument("--bcc", action="append", default=[])
    _add_smart_send_args(d_fwd)

    d_compose = _sub(dsub, "compose", help="Create new compose draft", schema_key="draft.compose")
    d_compose.add_argument("--subject", required=True)
    _add_body_args(d_compose)
    d_compose.add_argument("--to", action="append", default=[])
    d_compose.add_argument("--cc", action="append", default=[])
    d_compose.add_argument("--bcc", action="append", default=[])
    _add_smart_send_args(d_compose)

    d_read = _sub(dsub, "read", help="Read draft(s) with lifecycle", schema_key="draft.read")
    d_read.add_argument("thread_id")
    d_read.add_argument("--draft-id")
    d_read.add_argument("--account")
    d_read.add_argument("--active-only", action="store_true")

    d_status = _sub(dsub, "status", help="Read canonical draft lifecycle", schema_key="draft.status")
    d_status.add_argument("thread_id")
    d_status.add_argument("--draft-id")
    d_status.add_argument("--account")

    d_attest = _sub(dsub, "attest-render", help="Create exact live-render attestation", schema_key="draft.attest-render")
    d_attest.add_argument("thread_id")
    d_attest.add_argument("draft_id")
    d_attest.add_argument("--account", required=True)
    d_attest.add_argument("--output", required=True)
    d_attest.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    d_attest.add_argument("--window-id", type=int)
    d_attest.add_argument("--delay", type=int, default=20)

    d_prepare = _sub(dsub, "prepare", help="Trusted executor pre-approval render", schema_key="draft.prepare")
    d_prepare.add_argument("thread_id")
    d_prepare.add_argument("draft_id")
    d_prepare.add_argument("--account", required=True)
    d_prepare.add_argument("--delay", type=int, required=True)
    d_prepare.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    d_prepare.add_argument("--window-id", type=int)

    d_get = _sub(dsub, "get", help="Trusted executor render binding", schema_key="draft.get")
    d_get.add_argument("thread_id")
    d_get.add_argument("draft_id")
    d_get.add_argument("--account", required=True)
    d_get.add_argument("--attestation", required=True)
    d_get.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    d_get.add_argument("--window-id", type=int)

    d_send = _sub(dsub, "send", help="Trusted executor conditional send", schema_key="draft.send")
    d_send.add_argument("thread_id")
    d_send.add_argument("draft_id")
    d_send.add_argument("--account", required=True)
    d_send.add_argument("--attestation", required=True)
    d_send.add_argument("--if-revision", required=True)
    d_send.add_argument("--expected-draft-fingerprint", required=True)
    d_send.add_argument("--delay", type=int, required=True)
    d_send.add_argument("--wait", type=float, default=120)
    d_send.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    d_send.add_argument("--window-id", type=int)

    d_discard = _sub(dsub, "discard", help="Discard a draft", schema_key="draft.discard")
    d_discard.add_argument("thread_id")
    d_discard.add_argument("draft_id")

    d_attach = _sub(dsub, "attach", help="Attach file to draft", schema_key="draft.attach")
    d_attach.add_argument("thread_id")
    d_attach.add_argument("draft_id")
    d_attach.add_argument("file")
    d_attach.add_argument("--content-type", default="application/octet-stream")

    d_share = _sub(dsub, "share", help="Share a draft", schema_key="draft.share")
    d_share.add_argument("thread_id")
    d_share.add_argument("draft_id")
    d_share.add_argument("--name")

    d_unshare = _sub(dsub, "unshare", help="Unshare a draft", schema_key="draft.unshare")
    d_unshare.add_argument("thread_id")
    d_unshare.add_argument("draft_id")

    # -- comment --
    comment_p = _sub(sub, "comment", help="Comment operations")
    csub = comment_p.add_subparsers(dest="action")

    c_post = _sub(csub, "post", help="Post a comment", schema_key="comment.post")
    c_post.add_argument("thread_id")
    c_post.add_argument("--body", required=True)
    c_post.add_argument("--mention", nargs=2, metavar=("EMAIL", "NAME"), action="append")

    c_read = _sub(csub, "read", help="Read comments", schema_key="comment.read")
    c_read.add_argument("thread_id")

    c_read_many = _sub(csub, "read-many", help="Read comments for many threads", schema_key="comment.read-many")
    c_read_many.add_argument("thread_ids", nargs="+")
    c_read_many.add_argument("--batch-size", type=int, default=2)

    c_discard = _sub(csub, "discard", help="Delete a comment", schema_key="comment.discard")
    c_discard.add_argument("thread_id")
    c_discard.add_argument("comment_id")

    # -- send (top-level, irreversible) --
    send_p = _sub(sub, "send", help="Send a draft (IRREVERSIBLE — explicit or qualified-website policy)", schema_key="send")
    send_p.add_argument("thread_id")
    send_p.add_argument("draft_id")
    send_g = send_p.add_mutually_exclusive_group(required=True)
    send_g.add_argument("--dry-run", action="store_true", help="Metadata/lifecycle preflight without sending")
    send_g.add_argument("--status", action="store_true", help="Reconcile status without sending")
    send_g.add_argument("--confirm", action="store_true", help="Strict exact-attested send (irreversible)")
    send_g.add_argument(
        "--qualified-website-inbound",
        action="store_true",
        help="Policy-scoped send for a qualified website-inbound compose (irreversible)",
    )
    send_p.add_argument("--account")
    send_p.add_argument("--lead-email")
    send_p.add_argument("--qualification-ref")
    send_p.add_argument("--attestation")
    send_p.add_argument("--approval-receipt", help="Externally signed exact-send approval receipt")
    send_p.add_argument("--approval-ref", help="Deprecated audit correlation only; never grants authority")
    send_p.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    send_p.add_argument("--window-id", type=int)
    send_p.add_argument("--delay", type=int, default=20)
    send_p.add_argument("--wait", type=float, default=120)

    # -- attestation (read-only inspection) --
    attestation_p = _sub(sub, "attestation", help="Inspect exact render attestations")
    asub = attestation_p.add_subparsers(dest="action")
    a_show = _sub(asub, "show", help="Verify and show safe attestation metadata", schema_key="attestation.show")
    a_show.add_argument("reference")
    a_show.add_argument("--account")
    a_show.add_argument("--thread-id")
    a_show.add_argument("--draft-id")

    # -- approval (read-only external authority verification) --
    approval_p = _sub(sub, "approval", help="Verify externally issued exact-send approval receipts")
    apsub = approval_p.add_subparsers(dest="action")
    ap_verify = _sub(apsub, "verify", help="Verify receipt authority/binding/replay state", schema_key="approval.verify")
    ap_verify.add_argument("reference")
    ap_verify.add_argument("--attestation", required=True)

    # -- canonical executor status/abort (credential-free) --
    executor_p = _sub(sub, "executor", help="Canonical send-executor operations")
    exsub = executor_p.add_subparsers(dest="action")
    ex_status = _sub(exsub, "status", help="Read receipt execution state", schema_key="executor.status")
    ex_status.add_argument("receipt_id")
    ex_abort = _sub(exsub, "abort", help="Abort during grace", schema_key="executor.abort")
    ex_abort.add_argument("receipt_id")

    # -- executor contract (credential-free) --
    _sub(sub, "executor-contract", help="Report the fixed trusted executor provider contract", schema_key="executor-contract")

    # -- setup --
    setup_p = _sub(sub, "setup", help="Auto-detect credentials from local Superhuman app", schema_key="setup")
    setup_p.add_argument("--config", help="Output path for config.json")
    setup_p.add_argument("--email", help="Email account to bootstrap when multiple accounts are signed in")

    # -- doctor --
    _sub(sub, "doctor", help="Verify config, auth, and connectivity", schema_key="doctor")

    # -- schema --
    schema_p = _sub(sub, "schema", help="Introspect available commands", schema_key="schema")
    schema_p.add_argument("command_name", nargs="?", help="Specific command to describe")

    return p


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _typed_send_exit(data: dict[str, Any]) -> int:
    if data.get("sent") or data.get("state") == "scheduled":
        return 0
    if data.get("state") in {
        "send_requested",
        "send_pending_undo",
        "sent_backend_confirmed",
        "grace",
        "claimed",
        "accepted",
        "inconsistent",
        "unknown",
    }:
        return 4
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    raw_argv = list(argv) if argv is not None else list(sys.argv[1:])
    # Additive spelling from the lifecycle design while retaining the existing
    # flag-first ``shm send --dry-run THREAD DRAFT`` surface.
    if len(raw_argv) >= 2 and raw_argv[:2] == ["send", "status"]:
        raw_argv[1] = "--status"
    args = parser.parse_args(raw_argv)

    if not args.command:
        return emit(fail("shm", [error("input", "NO_COMMAND", False,
            f"No command specified. Available commands: {', '.join(_COMMANDS)}")]))

    # -- production reader --
    if args.command == "reader":
        if not hasattr(args, "action") or not args.action:
            return emit(fail("reader", [error("input", "MISSING_ACTION", False, "Use: shm reader scan")]))
        return emit(_reader.scan(
            since=args.since,
            before=args.before,
            accounts=args.account,
            projection=args.projection,
            threads=args.thread,
            people=args.person,
        ))

    # -- thread --
    if args.command == "thread":
        if not hasattr(args, "action") or not args.action:
            return emit(fail("thread", [error("input", "MISSING_ACTION", False, "Use: shm thread messages|userdata|list|search")]))
        elif args.action == "messages":
            return emit(_thread.messages(args.thread_id, account=args.account))
        elif args.action == "userdata":
            return emit(_thread.userdata(args.thread_id, account=args.account))
        elif args.action == "list":
            result = _thread.list_threads(limit=args.limit, unread=args.unread, include_participants=args.participants, account=args.account)
            if args.fail_empty and result["status"] == "succeeded" and result["data"]["returned"] == 0:
                return emit(result, exit_code=3)
            return emit(result)
        elif args.action == "search":
            result = _thread.search(args.query, limit=args.limit, unread=args.unread, include_participants=args.participants, account=args.account)
            if args.fail_empty and result["status"] == "succeeded" and result["data"]["returned"] == 0:
                return emit(result, exit_code=3)
            return emit(result)

    # -- attachment --
    elif args.command == "attachment":
        if not hasattr(args, "action") or not args.action:
            return emit(fail("attachment", [error("input", "MISSING_ACTION", False, "Use: shm attachment download")]))
        return emit(_attachment.download(
            args.thread_id,
            args.output,
            account=args.account,
            message_id=args.message_id,
            attachment_id=args.attachment_id,
        ))

    # -- opens --
    elif args.command == "opens":
        if args.thread_id and args.recent:
            return emit(fail("opens", [error("input", "CONFLICT", False, "Use either a thread_id or --recent, not both")]))
        elif args.recent:
            return emit(_opens.recent(limit=args.limit, recipient=args.recipient))
        elif args.thread_id:
            return emit(_opens.per_thread(args.thread_id, recipient=args.recipient))
        else:
            return emit(fail("opens", [error("input", "MISSING_ARG", False, "Provide a thread_id or use --recent")]))

    # -- draft --
    elif args.command == "draft":
        ss = {
            "scheduled_for": getattr(args, "scheduled_for", None),
            "abort_on_reply": getattr(args, "abort_on_reply", False),
            "reminder": getattr(args, "reminder", None),
            "sensitivity_label_id": getattr(args, "sensitivity_label_id", None),
            "sensitivity_tenant_id": getattr(args, "sensitivity_tenant_id", None),
        }
        if not hasattr(args, "action") or not args.action:
            return emit(fail("draft", [error("input", "MISSING_ACTION", False, "Use: shm draft reply|reply-all|forward|compose|read|status|attest-render|get|send|discard|attach|share|unshare")]))
        elif args.action in ("reply", "reply-all", "forward", "compose"):
            schema_key = f"draft.{args.action}"
            try:
                body, body_html = _validate_body(args, f"draft {args.action}", schema_key)
            except _BodyValidationError as e:
                return emit(fail(schema_key, [error("input", "BODY_REQUIRED", False, e.hint)]))
            if args.action == "reply":
                return emit(_draft.create_reply(args.thread_id, body, body_html=body_html, **ss))
            elif args.action == "reply-all":
                return emit(_draft.create_reply(args.thread_id, body, reply_all=True, body_html=body_html, **ss))
            elif args.action == "forward":
                return emit(_draft.create_forward(args.thread_id, body, to=args.to, cc=args.cc, bcc=args.bcc, body_html=body_html, **ss))
            elif args.action == "compose":
                return emit(_draft.create_compose(args.subject, body, to=args.to, cc=args.cc, bcc=args.bcc, body_html=body_html, **ss))
        elif args.action == "read":
            return emit(_draft.read(
                args.thread_id,
                draft_id=args.draft_id,
                account=args.account,
                active_only=args.active_only,
            ))
        elif args.action == "status":
            return emit(_draft.status(args.thread_id, draft_id=args.draft_id, account=args.account))
        elif args.action == "attest-render":
            try:
                record = _attestation.create(
                    args.thread_id,
                    args.draft_id,
                    account=args.account,
                    output_dir=Path(args.output),
                    delay=args.delay,
                    renderer=_attestation.CdpRenderer(cdp_url=args.cdp_url, window_id=args.window_id),
                )
                return emit(ok("draft.attest-render", {
                    "attestation_id": record["attestation_id"],
                    "artifact_path": record["artifact_path"],
                    "created_at": record["created_at"],
                    "expires_at": record["expires_at"],
                    "send_eligible": record["send_eligible"],
                    "confidence": record["confidence"],
                    "account_email": record["account"]["email"],
                    "thread_id": record["thread_id"],
                    "draft_id": record["draft_id"],
                    "fingerprint": record["fingerprint"]["exact"],
                    "renderer": record["renderer"],
                    "screenshots": record["screenshots"],
                }))
            except _attestation.AttestationError as exc:
                return emit(fail("draft.attest-render", [error("conflict", exc.code, False, exc.hint)]))
        elif args.action == "prepare":
            try:
                _executor.require_credential_bridge()
                return emit(ok("draft.prepare", _executor.prepare_attestation(
                    args.thread_id,
                    args.draft_id,
                    account=args.account,
                    delay=args.delay,
                    renderer=_attestation.CdpRenderer(cdp_url=args.cdp_url, window_id=args.window_id),
                )))
            except _executor.ExecutorContractError as exc:
                return emit(fail("draft.prepare", [error("conflict", exc.code, False, exc.hint)]))
            except _attestation.AttestationError as exc:
                return emit(fail("draft.prepare", [error("conflict", exc.code, False, exc.hint)]))
        elif args.action == "get":
            try:
                _executor.require_credential_bridge()
                return emit(ok("draft.get", _executor.get_rendered(
                    args.thread_id,
                    args.draft_id,
                    account=args.account,
                    attestation_reference=args.attestation,
                    renderer=_attestation.CdpRenderer(cdp_url=args.cdp_url, window_id=args.window_id),
                )))
            except _executor.ExecutorContractError as exc:
                return emit(fail("draft.get", [error("conflict", exc.code, False, exc.hint)]))
            except _attestation.AttestationError as exc:
                return emit(fail("draft.get", [error("conflict", exc.code, False, exc.hint)]))
        elif args.action == "send":
            try:
                _executor.require_credential_bridge()
                return emit(ok("draft.send", _executor.send_conditional(
                    args.thread_id,
                    args.draft_id,
                    account=args.account,
                    attestation_reference=args.attestation,
                    if_revision=args.if_revision,
                    expected_draft_fingerprint=args.expected_draft_fingerprint,
                    delay=args.delay,
                    wait=args.wait,
                    renderer=_attestation.CdpRenderer(cdp_url=args.cdp_url, window_id=args.window_id),
                )))
            except _executor.ExecutorContractError as exc:
                return emit(
                    fail("draft.send", [error("conflict", exc.code, False, exc.hint)]),
                    exit_code=10,
                )
            except _attestation.AttestationError as exc:
                return emit(fail("draft.send", [error("conflict", exc.code, False, exc.hint)]), exit_code=10)
        elif args.action == "discard":
            return emit(_draft.discard(args.thread_id, args.draft_id))
        elif args.action == "attach":
            return emit(_draft.attach(args.thread_id, args.draft_id, args.file, content_type=args.content_type))
        elif args.action == "share":
            return emit(_share.share(args.thread_id, args.draft_id, name=args.name))
        elif args.action == "unshare":
            return emit(_share.unshare(args.thread_id, args.draft_id))

    # -- comment --
    elif args.command == "comment":
        if not hasattr(args, "action") or not args.action:
            return emit(fail("comment", [error("input", "MISSING_ACTION", False, "Use: shm comment post|read|read-many|discard")]))
        elif args.action == "post":
            mentions = [{"email": m[0], "fullName": m[1]} for m in (args.mention or [])]
            return emit(_comment.post(args.thread_id, args.body, mentions=mentions or None))
        elif args.action == "read":
            return emit(_comment.read(args.thread_id))
        elif args.action == "read-many":
            return emit(_comment.read_many(args.thread_ids, batch_size=args.batch_size))
        elif args.action == "discard":
            return emit(_comment.discard(args.thread_id, args.comment_id))

    # -- send --
    elif args.command == "send":
        if args.dry_run:
            return emit(_send.validate(args.thread_id, args.draft_id, account=args.account))
        elif args.status:
            if not args.account:
                return emit(fail("send.status", [error("input", "ACCOUNT_REQUIRED", False, "--account is required")]))
            result = _send.status(
                args.thread_id,
                args.draft_id,
                account=args.account,
                wait=args.wait,
            )
            if result["status"] == "succeeded":
                return emit(result, exit_code=_typed_send_exit(result["data"]))
            return emit(result)
        elif args.qualified_website_inbound:
            result = _send.execute_qualified_website_inbound(
                args.thread_id,
                args.draft_id,
                delay=args.delay,
                account=args.account,
                lead_email=args.lead_email,
                qualification_ref=args.qualification_ref,
                wait=args.wait,
            )
            if result["status"] == "succeeded":
                return emit(result, exit_code=_typed_send_exit(result["data"]))
            return emit(result)
        elif args.confirm:
            result = _send.execute(
                args.thread_id,
                args.draft_id,
                delay=args.delay,
                account=args.account,
                attestation=args.attestation,
                approval_receipt=args.approval_receipt,
                approval_ref=args.approval_ref,
                wait=args.wait,
                renderer=_attestation.CdpRenderer(cdp_url=args.cdp_url, window_id=args.window_id),
            )
            if result["status"] == "succeeded":
                return emit(result, exit_code=_typed_send_exit(result["data"]))
            return emit(result)

    # -- attestation --
    elif args.command == "attestation":
        if not hasattr(args, "action") or not args.action:
            return emit(fail("attestation", [error("input", "MISSING_ACTION", False, "Use: shm attestation show")]))
        if args.action == "show":
            try:
                summary = _attestation.show_safe(
                    args.reference,
                    account=args.account,
                    thread_id=args.thread_id,
                    draft_id=args.draft_id,
                )
                return emit(ok("attestation.show", summary))
            except _attestation.AttestationError as exc:
                return emit(fail("attestation.show", [error("conflict", exc.code, False, exc.hint)]))

    # -- approval --
    elif args.command == "approval":
        if not hasattr(args, "action") or not args.action:
            return emit(fail("approval", [error("input", "MISSING_ACTION", False, "Use: shm approval verify")]))
        if args.action == "verify":
            try:
                return emit(ok(
                    "approval.verify",
                    _approval.show_safe(args.reference, attestation_reference=args.attestation),
                ))
            except _approval.ApprovalError as exc:
                return emit(fail("approval.verify", [error("conflict", exc.code, False, exc.hint)]))
            except _attestation.AttestationError as exc:
                return emit(fail("approval.verify", [error("conflict", exc.code, False, exc.hint)]))

    # -- canonical executor status/abort --
    elif args.command == "executor":
        if not getattr(args, "action", None):
            return emit(fail("executor", [error("input", "MISSING_ACTION", False, "Use: shm executor status|abort RECEIPT_ID")]))
        result = _authority_client.status(args.receipt_id) if args.action == "status" else _authority_client.abort(args.receipt_id)
        return emit(result)

    # -- credential-free executor contract --
    elif args.command == "executor-contract":
        return emit(ok("executor-contract", _executor.CONTRACT))

    # -- setup --
    elif args.command == "setup":
        try:
            config_path = Path(args.config) if args.config else None
            result = _setup.run_setup(config_path=config_path, email=args.email)
            return emit(ok("setup", result))
        except Exception as e:
            return emit(fail("setup", [error("input", "SETUP_FAILED", False, str(e))]))

    # -- doctor --
    elif args.command == "doctor":
        return emit(_doctor())

    # -- schema --
    elif args.command == "schema":
        if args.command_name:
            if args.command_name in SCHEMA:
                return emit(ok("schema", SCHEMA[args.command_name]))
            else:
                return emit(fail("schema", [error("not-found", "UNKNOWN_COMMAND", False, f"Unknown command: {args.command_name}. Run `shm schema` for the full list.")]))
        else:
            summary = {name: {"description": s["description"], "safety": s["safety"]} for name, s in SCHEMA.items()}
            return emit(ok("schema", {"commands": summary}))

    else:
        return emit(fail("shm", [error("input", "UNKNOWN_COMMAND", False,
            f"Unknown command: {args.command}. Available: {', '.join(_COMMANDS)}")]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
