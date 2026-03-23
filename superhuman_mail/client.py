"""High-level Python client wrapping all Superhuman operations.

Usage:
    from superhuman_mail import Client

    c = Client()
    result = c.thread.messages("19d001f35612a211")
    result = c.opens.per_thread("19d001f35612a211")
    result = c.opens.recent(limit=10)
    result = c.draft.create_reply("19d001f35612a211", body="Thanks!")
    result = c.send.validate("19d001f35612a211", "draft00abc")
"""
from __future__ import annotations

from typing import Any

from . import comment as _comment
from . import draft as _draft
from . import opens as _opens
from . import send as _send
from . import share as _share
from . import thread as _thread


class _ThreadOps:
    """Thread operations."""

    def messages(self, thread_id: str, account: str | None = None) -> dict[str, Any]:
        """Read messages from local DB."""
        return _thread.messages(thread_id, account)

    def read(self, thread_id: str, account: str | None = None) -> dict[str, Any]:
        """Backward-compatible alias for messages()."""
        return self.messages(thread_id, account)

    def userdata(self, thread_id: str) -> dict[str, Any]:
        """Read thread userdata from API."""
        return _thread.userdata(thread_id)

    def list(self, **kwargs: Any) -> dict[str, Any]:
        """List recent threads."""
        return _thread.list_threads(**kwargs)

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """Search threads."""
        return _thread.search(query, **kwargs)


class _OpensOps:
    """Read receipts / opens operations."""

    def per_thread(self, thread_id: str, recipient: str | None = None) -> dict[str, Any]:
        """Read per-thread opens / read receipts."""
        return _opens.per_thread(thread_id, recipient=recipient)

    def recent(self, *, limit: int = 20, recipient: str | None = None, account: str | None = None) -> dict[str, Any]:
        """Read recent opens from the local activity feed."""
        return _opens.recent(limit=limit, recipient=recipient, account=account)


class _DraftOps:
    """Draft operations."""

    def create_reply(self, thread_id: str, body: str, **kwargs: Any) -> dict[str, Any]:
        """Create a reply draft."""
        return _draft.create_reply(thread_id, body, **kwargs)

    def create_reply_all(self, thread_id: str, body: str, **kwargs: Any) -> dict[str, Any]:
        """Create a reply-all draft."""
        return _draft.create_reply(thread_id, body, reply_all=True, **kwargs)

    def create_forward(self, thread_id: str, body: str, **kwargs: Any) -> dict[str, Any]:
        """Create a forward draft."""
        return _draft.create_forward(thread_id, body, **kwargs)

    def create_compose(self, subject: str, body: str, **kwargs: Any) -> dict[str, Any]:
        """Create a new compose draft."""
        return _draft.create_compose(subject, body, **kwargs)

    def read(self, thread_id: str, draft_id: str | None = None) -> dict[str, Any]:
        """Read draft(s) from thread."""
        return _draft.read(thread_id, draft_id)

    def discard(self, thread_id: str, draft_id: str) -> dict[str, Any]:
        """Discard a draft."""
        return _draft.discard(thread_id, draft_id)

    def attach(self, thread_id: str, draft_id: str, filepath: str, **kwargs: Any) -> dict[str, Any]:
        """Attach a file to a draft."""
        return _draft.attach(thread_id, draft_id, filepath, **kwargs)

    def share(self, thread_id: str, draft_id: str, **kwargs: Any) -> dict[str, Any]:
        """Share a draft."""
        return _share.share(thread_id, draft_id, **kwargs)

    def unshare(self, thread_id: str, draft_id: str) -> dict[str, Any]:
        """Unshare a draft."""
        return _share.unshare(thread_id, draft_id)


class _CommentOps:
    """Comment operations."""

    def post(self, thread_id: str, body: str, mentions: list[dict[str, str]] | None = None) -> dict[str, Any]:
        """Post a comment."""
        return _comment.post(thread_id, body, mentions)

    def read(self, thread_id: str) -> dict[str, Any]:
        """Read comments."""
        return _comment.read(thread_id)

    def discard(self, thread_id: str, comment_id: str) -> dict[str, Any]:
        """Discard a comment."""
        return _comment.discard(thread_id, comment_id)


class _SendOps:
    """Send operations (irreversible)."""

    def validate(self, thread_id: str, draft_id: str) -> dict[str, Any]:
        """Dry-run: validate a draft is sendable."""
        return _send.validate(thread_id, draft_id)

    def execute(self, thread_id: str, draft_id: str, **kwargs: Any) -> dict[str, Any]:
        """Send a draft. IRREVERSIBLE."""
        return _send.execute(thread_id, draft_id, **kwargs)


class _ShareOps:
    """Backward-compatible share/unshare wrapper."""

    def share(self, thread_id: str, draft_id: str, **kwargs: Any) -> dict[str, Any]:
        """Share a draft."""
        return _share.share(thread_id, draft_id, **kwargs)

    def unshare(self, thread_id: str, draft_id: str) -> dict[str, Any]:
        """Unshare a draft."""
        return _share.unshare(thread_id, draft_id)


class Client:
    """Superhuman Mail API client.

    Groups operations by domain:
        client.thread.messages(...)
        client.opens.per_thread(...)
        client.draft.create_reply(...)
        client.comment.post(...)
        client.send.validate(...)
    """

    def __init__(self) -> None:
        self.thread = _ThreadOps()
        self.opens = _OpensOps()
        self.draft = _DraftOps()
        self.comment = _CommentOps()
        self.send = _SendOps()
        self.share = _ShareOps()
