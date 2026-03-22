"""CLI entry point for shm — Superhuman Mail agent-friendly CLI.

Usage:
    shm thread read <thread_id>
    shm draft reply <thread_id> --body "..."
    shm send --dry-run <thread_id> <draft_id>
    shm doctor
    shm schema [command]
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import _auth, _config, _local
from . import comment as _comment
from . import draft as _draft
from . import send as _send
from . import share as _share
from . import thread as _thread
from ._envelope import emit, error, fail, ok

# ---------------------------------------------------------------------------
# Schema definition (for agent introspection)
# ---------------------------------------------------------------------------

SCHEMA: dict[str, dict[str, Any]] = {
    "thread.read": {
        "description": "Read thread messages from local Superhuman DB",
        "args": {"thread_id": {"required": True, "type": "string"}},
        "safety": "read",
        "example": "shm thread read 19d001f35612a211",
    },
    "thread.userdata": {
        "description": "Read thread userdata (drafts, comments, metadata) from API",
        "args": {"thread_id": {"required": True, "type": "string"}},
        "safety": "read",
        "example": "shm thread userdata 19d001f35612a211",
    },
    "draft.reply": {
        "description": "Create a reply draft on an existing thread",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--body": {"required": True, "type": "string"},
            "--body-html": {"required": False, "type": "string"},
            "--scheduled-for": {"required": False, "type": "string", "hint": "ISO datetime"},
        },
        "safety": "write",
        "example": "shm draft reply 19d001f35612a211 --body 'Thanks for the update'",
    },
    "draft.reply-all": {
        "description": "Create a reply-all draft on an existing thread",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--body": {"required": True, "type": "string"},
            "--body-html": {"required": False, "type": "string"},
            "--scheduled-for": {"required": False, "type": "string"},
        },
        "safety": "write",
        "example": "shm draft reply-all 19d001f35612a211 --body 'Sounds good to everyone'",
    },
    "draft.forward": {
        "description": "Create a forward draft on an existing thread",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--body": {"required": True, "type": "string"},
            "--body-html": {"required": False, "type": "string"},
            "--scheduled-for": {"required": False, "type": "string"},
        },
        "safety": "write",
        "example": "shm draft forward 19d001f35612a211 --body 'FYI — see below'",
    },
    "draft.compose": {
        "description": "Create a new compose draft (new thread)",
        "args": {
            "--subject": {"required": True, "type": "string"},
            "--body": {"required": True, "type": "string"},
            "--to": {"required": False, "type": "string[]", "hint": "Repeatable"},
            "--cc": {"required": False, "type": "string[]"},
            "--bcc": {"required": False, "type": "string[]"},
            "--body-html": {"required": False, "type": "string"},
            "--scheduled-for": {"required": False, "type": "string"},
        },
        "safety": "write",
        "example": "shm draft compose --subject 'Hello' --body 'Hi there' --to someone@example.com",
    },
    "draft.read": {
        "description": "Read draft(s) from a thread's userdata",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--draft-id": {"required": False, "type": "string"},
        },
        "safety": "read",
        "example": "shm draft read 19d001f35612a211",
    },
    "draft.discard": {
        "description": "Discard (soft-delete) a draft",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
        },
        "safety": "write",
        "example": "shm draft discard 19d001f35612a211 draft00abc123",
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
        "example": "shm draft attach 19d001f35612a211 draft00abc123 ./report.pdf",
    },
    "comment.post": {
        "description": "Post a comment on a thread",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "--body": {"required": True, "type": "string"},
            "--mention": {"required": False, "type": "pair[]", "hint": "EMAIL NAME, repeatable"},
        },
        "safety": "write",
        "example": "shm comment post 19d001f35612a211 --body 'Please review'",
    },
    "comment.read": {
        "description": "Read all comments on a thread",
        "args": {"thread_id": {"required": True, "type": "string"}},
        "safety": "read",
        "example": "shm comment read 19d001f35612a211",
    },
    "comment.discard": {
        "description": "Delete a comment from a thread",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "comment_id": {"required": True, "type": "string"},
        },
        "safety": "write",
        "example": "shm comment discard 19d001f35612a211 cmt_1abc123",
    },
    "send": {
        "description": "Send a draft — IRREVERSIBLE. Requires --dry-run or --confirm.",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
            "--dry-run": {"required": False, "type": "flag", "hint": "Validate without sending"},
            "--confirm": {"required": False, "type": "flag", "hint": "Actually send (irreversible)"},
            "--delay": {"required": False, "type": "int", "default": 20},
        },
        "safety": "irreversible",
        "example": "shm send --dry-run 19d001f35612a211 draft00abc123",
    },
    "share": {
        "description": "Share a draft with a collaboration link",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
            "--name": {"required": False, "type": "string"},
        },
        "safety": "write",
        "example": "shm share 19d001f35612a211 draft00abc123",
    },
    "unshare": {
        "description": "Remove sharing from a draft",
        "args": {
            "thread_id": {"required": True, "type": "string"},
            "draft_id": {"required": True, "type": "string"},
        },
        "safety": "write",
        "example": "shm unshare 19d001f35612a211 draft00abc123",
    },
    "doctor": {
        "description": "Verify config, auth, and connectivity",
        "args": {},
        "safety": "read",
        "example": "shm doctor",
    },
    "schema": {
        "description": "Introspect available commands",
        "args": {"command": {"required": False, "type": "string"}},
        "safety": "read",
        "example": "shm schema draft.reply",
    },
}

# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------


def _doctor() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    # 1. Config
    try:
        cfg = _config.load()
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
# Argparse setup
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shm",
        description="Superhuman Mail — agent-friendly CLI for the unofficial Superhuman API",
    )
    sub = p.add_subparsers(dest="command")

    # -- thread --
    thread_p = sub.add_parser("thread", help="Thread operations")
    tsub = thread_p.add_subparsers(dest="action")
    t_read = tsub.add_parser("read", help="Read messages from local DB")
    t_read.add_argument("thread_id")
    t_ud = tsub.add_parser("userdata", help="Read userdata from API")
    t_ud.add_argument("thread_id")

    # -- draft --
    draft_p = sub.add_parser("draft", help="Draft operations")
    dsub = draft_p.add_subparsers(dest="action")

    d_reply = dsub.add_parser("reply", help="Create reply draft")
    d_reply.add_argument("thread_id")
    d_reply.add_argument("--body", required=True)
    d_reply.add_argument("--body-html")
    d_reply.add_argument("--scheduled-for")

    d_ra = dsub.add_parser("reply-all", help="Create reply-all draft")
    d_ra.add_argument("thread_id")
    d_ra.add_argument("--body", required=True)
    d_ra.add_argument("--body-html")
    d_ra.add_argument("--scheduled-for")

    d_fwd = dsub.add_parser("forward", help="Create forward draft")
    d_fwd.add_argument("thread_id")
    d_fwd.add_argument("--body", required=True)
    d_fwd.add_argument("--body-html")
    d_fwd.add_argument("--scheduled-for")

    d_compose = dsub.add_parser("compose", help="Create new compose draft")
    d_compose.add_argument("--subject", required=True)
    d_compose.add_argument("--body", required=True)
    d_compose.add_argument("--to", action="append", default=[])
    d_compose.add_argument("--cc", action="append", default=[])
    d_compose.add_argument("--bcc", action="append", default=[])
    d_compose.add_argument("--body-html")
    d_compose.add_argument("--scheduled-for")

    d_read = dsub.add_parser("read", help="Read draft(s)")
    d_read.add_argument("thread_id")
    d_read.add_argument("--draft-id")

    d_discard = dsub.add_parser("discard", help="Discard a draft")
    d_discard.add_argument("thread_id")
    d_discard.add_argument("draft_id")

    d_attach = dsub.add_parser("attach", help="Attach file to draft")
    d_attach.add_argument("thread_id")
    d_attach.add_argument("draft_id")
    d_attach.add_argument("file")
    d_attach.add_argument("--content-type", default="application/octet-stream")

    # -- comment --
    comment_p = sub.add_parser("comment", help="Comment operations")
    csub = comment_p.add_subparsers(dest="action")

    c_post = csub.add_parser("post", help="Post a comment")
    c_post.add_argument("thread_id")
    c_post.add_argument("--body", required=True)
    c_post.add_argument("--mention", nargs=2, metavar=("EMAIL", "NAME"), action="append")

    c_read = csub.add_parser("read", help="Read comments")
    c_read.add_argument("thread_id")

    c_discard = csub.add_parser("discard", help="Delete a comment")
    c_discard.add_argument("thread_id")
    c_discard.add_argument("comment_id")

    # -- send (top-level, irreversible) --
    send_p = sub.add_parser("send", help="Send a draft (IRREVERSIBLE — requires --dry-run or --confirm)")
    send_p.add_argument("thread_id")
    send_p.add_argument("draft_id")
    send_g = send_p.add_mutually_exclusive_group(required=True)
    send_g.add_argument("--dry-run", action="store_true", help="Validate without sending")
    send_g.add_argument("--confirm", action="store_true", help="Actually send (irreversible)")
    send_p.add_argument("--delay", type=int, default=20)

    # -- share / unshare --
    share_p = sub.add_parser("share", help="Share a draft")
    share_p.add_argument("thread_id")
    share_p.add_argument("draft_id")
    share_p.add_argument("--name")

    unshare_p = sub.add_parser("unshare", help="Unshare a draft")
    unshare_p.add_argument("thread_id")
    unshare_p.add_argument("draft_id")

    # -- doctor --
    sub.add_parser("doctor", help="Verify config, auth, and connectivity")

    # -- schema --
    schema_p = sub.add_parser("schema", help="Introspect available commands")
    schema_p.add_argument("command_name", nargs="?", help="Specific command to describe")

    return p


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    # -- thread --
    if args.command == "thread":
        if not hasattr(args, "action") or not args.action:
            emit(fail("thread", [error("input", "MISSING_ACTION", False, "Use: shm thread read|userdata")]))
        elif args.action == "read":
            emit(_thread.read(args.thread_id))
        elif args.action == "userdata":
            emit(_thread.userdata(args.thread_id))

    # -- draft --
    elif args.command == "draft":
        if not hasattr(args, "action") or not args.action:
            emit(fail("draft", [error("input", "MISSING_ACTION", False, "Use: shm draft reply|reply-all|forward|compose|read|discard|attach")]))
        elif args.action == "reply":
            emit(_draft.create_reply(args.thread_id, args.body, body_html=args.body_html, scheduled_for=args.scheduled_for))
        elif args.action == "reply-all":
            emit(_draft.create_reply(args.thread_id, args.body, reply_all=True, body_html=args.body_html, scheduled_for=args.scheduled_for))
        elif args.action == "forward":
            emit(_draft.create_forward(args.thread_id, args.body, body_html=args.body_html, scheduled_for=args.scheduled_for))
        elif args.action == "compose":
            emit(_draft.create_compose(args.subject, args.body, to=args.to, cc=args.cc, bcc=args.bcc, body_html=args.body_html, scheduled_for=args.scheduled_for))
        elif args.action == "read":
            emit(_draft.read(args.thread_id, draft_id=args.draft_id))
        elif args.action == "discard":
            emit(_draft.discard(args.thread_id, args.draft_id))
        elif args.action == "attach":
            emit(_draft.attach(args.thread_id, args.draft_id, args.file, content_type=args.content_type))

    # -- comment --
    elif args.command == "comment":
        if not hasattr(args, "action") or not args.action:
            emit(fail("comment", [error("input", "MISSING_ACTION", False, "Use: shm comment post|read|discard")]))
        elif args.action == "post":
            mentions = [{"email": m[0], "fullName": m[1]} for m in (args.mention or [])]
            emit(_comment.post(args.thread_id, args.body, mentions=mentions or None))
        elif args.action == "read":
            emit(_comment.read(args.thread_id))
        elif args.action == "discard":
            emit(_comment.discard(args.thread_id, args.comment_id))

    # -- send --
    elif args.command == "send":
        if args.dry_run:
            emit(_send.validate(args.thread_id, args.draft_id))
        elif args.confirm:
            emit(_send.execute(args.thread_id, args.draft_id, delay=args.delay))

    # -- share / unshare --
    elif args.command == "share":
        emit(_share.share(args.thread_id, args.draft_id, name=args.name))
    elif args.command == "unshare":
        emit(_share.unshare(args.thread_id, args.draft_id))

    # -- doctor --
    elif args.command == "doctor":
        emit(_doctor())

    # -- schema --
    elif args.command == "schema":
        if args.command_name:
            if args.command_name in SCHEMA:
                emit(ok("schema", SCHEMA[args.command_name]))
            else:
                emit(fail("schema", [error("not-found", "UNKNOWN_COMMAND", False, f"Unknown command: {args.command_name}. Run `shm schema` for the full list.")]))
        else:
            summary = {name: {"description": s["description"], "safety": s["safety"]} for name, s in SCHEMA.items()}
            emit(ok("schema", {"commands": summary}))

    else:
        parser.print_help()
        return 1

    return 0  # emit() calls sys.exit, so this is only reached if no command matched
