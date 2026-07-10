"""Local idempotent send-attempt journal.

The guarantee is deliberately scoped to cooperating ``shm`` processes sharing
one canonical state directory.  It does not claim cross-host or native-UI
serialization without a vendor idempotency/CAS contract.
"""
from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ._state import private_file, state_dir

IDEMPOTENCY_SCOPE = "local_cooperating_processes"
TERMINAL_ATTEMPT_STATES = {
    "sent_provider_confirmed",
    "send_failed",
    "send_aborted",
}
PURGEABLE_ATTEMPT_STATES = {
    "send_failed",
    "send_aborted",
}


class AttemptConflict(RuntimeError):
    """An existing attempt cannot be reused for a different approval/payload."""


class ReceiptClaimError(RuntimeError):
    """A verified approval receipt cannot be atomically consumed."""

    def __init__(self, code: str, hint: str) -> None:
        super().__init__(hint)
        self.code = code
        self.hint = hint


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _hash_ref(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


class AttemptJournal:
    """SQLite journal with an atomic one-POST claim per account/draft."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (state_dir() / "attempts.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._initialize()
        self.purge_terminal(retention_days=30)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            # Concurrent first-open may briefly hold the journal-mode lock; the
            # winning connection establishes WAL before either can claim work.
            if "locked" not in str(exc).lower():
                raise
        conn.execute("PRAGMA synchronous=FULL")
        private_file(self.path)
        private_file(Path(str(self.path) + "-wal"))
        private_file(Path(str(self.path) + "-shm"))
        return conn

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    account_hash TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    attestation_id TEXT NOT NULL,
                    approval_ref_hash TEXT NOT NULL,
                    approval_receipt_id TEXT,
                    approval_receipt_digest TEXT,
                    approval_issuer TEXT,
                    approval_key_id TEXT,
                    approval_approver TEXT,
                    approval_issued_at TEXT,
                    approval_expires_at TEXT,
                    superhuman_id TEXT NOT NULL,
                    outgoing_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    post_count INTEGER NOT NULL DEFAULT 0,
                    response_class TEXT,
                    last_error TEXT,
                    last_reconciled_at TEXT,
                    provider_message_id TEXT,
                    backend_sent_at TEXT,
                    provider_sent_at TEXT,
                    UNIQUE(account_id, draft_id)
                );
                CREATE INDEX IF NOT EXISTS attempts_updated_at ON attempts(updated_at);
                CREATE TABLE IF NOT EXISTS approval_receipt_consumptions (
                    receipt_id TEXT PRIMARY KEY,
                    receipt_digest TEXT NOT NULL,
                    attempt_id TEXT NOT NULL UNIQUE,
                    consumed_at TEXT NOT NULL,
                    FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
                );
                """
            )
            conn.execute("BEGIN IMMEDIATE")
            migrations = {
                "approval_receipt_id": "TEXT",
                "approval_receipt_digest": "TEXT",
                "approval_issuer": "TEXT",
                "approval_key_id": "TEXT",
                "approval_approver": "TEXT",
                "approval_issued_at": "TEXT",
                "approval_expires_at": "TEXT",
            }
            for column, column_type in migrations.items():
                columns = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
                if column not in columns:
                    conn.execute(f"ALTER TABLE attempts ADD COLUMN {column} {column_type}")
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
            private_file(self.path)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def get(self, account_id: str, draft_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            return self._row(
                conn.execute(
                    "SELECT * FROM attempts WHERE account_id = ? AND draft_id = ?",
                    (account_id, draft_id),
                ).fetchone()
            )
        finally:
            conn.close()

    def get_by_id(self, attempt_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            return self._row(conn.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone())
        finally:
            conn.close()

    def get_receipt_consumption(self, receipt_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            return self._row(
                conn.execute(
                    "SELECT * FROM approval_receipt_consumptions WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()
            )
        finally:
            conn.close()

    def create_or_get(
        self,
        *,
        account_id: str,
        account_hash: str,
        thread_id: str,
        draft_id: str,
        attestation_id: str,
        approval_receipt_id: str,
        approval_receipt_digest: str,
        approval_issuer: str,
        approval_key_id: str,
        approval_approver: str,
        approval_issued_at: str,
        approval_expires_at: str,
        superhuman_id: str,
        outgoing_fingerprint: str,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically create one attempt or return its exact compatible row."""
        now = _now()
        approval_hash = _hash_ref(approval_receipt_id)
        attempt_id = str(uuid.uuid4())
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM attempts WHERE account_id = ? AND draft_id = ?",
                (account_id, draft_id),
            ).fetchone()
            if existing is not None:
                row = dict(existing)
                expected = {
                    "thread_id": thread_id,
                    "attestation_id": attestation_id,
                    "approval_ref_hash": approval_hash,
                    "approval_receipt_id": approval_receipt_id,
                    "approval_receipt_digest": approval_receipt_digest,
                    "approval_issuer": approval_issuer,
                    "approval_key_id": approval_key_id,
                    "approval_approver": approval_approver,
                    "approval_issued_at": approval_issued_at,
                    "approval_expires_at": approval_expires_at,
                    "superhuman_id": superhuman_id,
                    "outgoing_fingerprint": outgoing_fingerprint,
                }
                mismatched = [key for key, value in expected.items() if row.get(key) != value]
                if mismatched:
                    if row["state"] == "prepared" and int(row["post_count"]) == 0:
                        # No process has claimed network I/O. Atomically rotate
                        # the primary attempt ID so a stale concurrent holder
                        # cannot claim this replacement with its old payload.
                        conn.execute(
                            """
                            UPDATE attempts SET
                                attempt_id = ?, account_hash = ?, thread_id = ?,
                                attestation_id = ?, approval_ref_hash = ?,
                                approval_receipt_id = ?, approval_receipt_digest = ?,
                                approval_issuer = ?, approval_key_id = ?,
                                approval_approver = ?, approval_issued_at = ?, approval_expires_at = ?,
                                superhuman_id = ?, outgoing_fingerprint = ?,
                                created_at = ?, updated_at = ?, state = 'prepared',
                                response_class = NULL, last_error = NULL,
                                last_reconciled_at = NULL, provider_message_id = NULL,
                                backend_sent_at = NULL, provider_sent_at = NULL
                            WHERE attempt_id = ? AND post_count = 0 AND state = 'prepared'
                            """,
                            (
                                attempt_id,
                                account_hash,
                                thread_id,
                                attestation_id,
                                approval_hash,
                                approval_receipt_id,
                                approval_receipt_digest,
                                approval_issuer,
                                approval_key_id,
                                approval_approver,
                                approval_issued_at,
                                approval_expires_at,
                                superhuman_id,
                                outgoing_fingerprint,
                                now,
                                now,
                                row["attempt_id"],
                            ),
                        )
                        replacement = conn.execute(
                            "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
                        ).fetchone()
                        conn.execute("COMMIT")
                        assert replacement is not None
                        return dict(replacement), True
                    conn.execute("ROLLBACK")
                    raise AttemptConflict(
                        "Existing attempt differs in " + ", ".join(mismatched) + "; reconcile it instead of creating a new identity"
                    )
                conn.execute("COMMIT")
                return row, False

            conn.execute(
                """
                INSERT INTO attempts (
                    attempt_id, account_id, account_hash, thread_id, draft_id,
                    attestation_id, approval_ref_hash, approval_receipt_id,
                    approval_receipt_digest, approval_issuer, approval_key_id,
                    approval_approver, approval_issued_at, approval_expires_at,
                    superhuman_id, outgoing_fingerprint, created_at, updated_at, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared')
                """,
                (
                    attempt_id,
                    account_id,
                    account_hash,
                    thread_id,
                    draft_id,
                    attestation_id,
                    approval_hash,
                    approval_receipt_id,
                    approval_receipt_digest,
                    approval_issuer,
                    approval_key_id,
                    approval_approver,
                    approval_issued_at,
                    approval_expires_at,
                    superhuman_id,
                    outgoing_fingerprint,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            conn.execute("COMMIT")
            assert row is not None
            return dict(row), True
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def claim_post(
        self,
        attempt_id: str,
        *,
        approval_receipt_id: str,
        approval_receipt_digest: str,
        approval_expires_at: str,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically consume one approval receipt and claim the only POST."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(attempt_id)
            current = dict(row)
            if current["state"] != "prepared" or int(current["post_count"]) != 0:
                conn.execute("COMMIT")
                return current, False
            if (
                current.get("approval_receipt_id") != approval_receipt_id
                or current.get("approval_receipt_digest") != approval_receipt_digest
                or current.get("approval_expires_at") != approval_expires_at
            ):
                conn.execute("ROLLBACK")
                raise ReceiptClaimError(
                    "APPROVAL_BINDING_MISMATCH",
                    "Attempt and approval receipt bindings differ",
                )
            now_dt = datetime.now(timezone.utc)
            try:
                expires_dt = datetime.fromisoformat(approval_expires_at.replace("Z", "+00:00"))
                if expires_dt.tzinfo is None:
                    expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            except ValueError as exc:
                conn.execute("ROLLBACK")
                raise ReceiptClaimError("APPROVAL_RECEIPT_INVALID", "Receipt expiry is malformed") from exc
            if expires_dt.astimezone(timezone.utc) <= now_dt:
                conn.execute("ROLLBACK")
                raise ReceiptClaimError("APPROVAL_RECEIPT_EXPIRED", "Receipt expired before POST claim")
            consumed = conn.execute(
                "SELECT * FROM approval_receipt_consumptions WHERE receipt_id = ?",
                (approval_receipt_id,),
            ).fetchone()
            if consumed is not None:
                conn.execute("ROLLBACK")
                raise ReceiptClaimError("APPROVAL_RECEIPT_REPLAYED", "Approval receipt was already consumed")
            now = now_dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
            conn.execute(
                """
                INSERT INTO approval_receipt_consumptions (
                    receipt_id, receipt_digest, attempt_id, consumed_at
                ) VALUES (?, ?, ?, ?)
                """,
                (approval_receipt_id, approval_receipt_digest, attempt_id, now),
            )
            conn.execute(
                "UPDATE attempts SET state = 'posting', post_count = 1, updated_at = ? WHERE attempt_id = ?",
                (now, attempt_id),
            )
            claimed = conn.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            conn.execute("COMMIT")
            assert claimed is not None
            return dict(claimed), True
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def update(self, attempt_id: str, *, state: str | None = None, **fields: Any) -> dict[str, Any]:
        allowed = {
            "response_class",
            "last_error",
            "last_reconciled_at",
            "provider_message_id",
            "backend_sent_at",
            "provider_sent_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown attempt fields: {', '.join(sorted(unknown))}")
        updates: dict[str, Any] = {**fields, "updated_at": _now()}
        if state is not None:
            updates["state"] = state
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [attempt_id]
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(f"UPDATE attempts SET {assignments} WHERE attempt_id = ?", values)
            if changed.rowcount != 1:
                conn.execute("ROLLBACK")
                raise KeyError(attempt_id)
            row = conn.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            conn.execute("COMMIT")
            assert row is not None
            return dict(row)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def purge_terminal(self, *, retention_days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        placeholders = ",".join("?" for _ in PURGEABLE_ATTEMPT_STATES)
        conn = self._connect()
        try:
            changed = conn.execute(
                f"DELETE FROM attempts WHERE state IN ({placeholders}) AND updated_at < ?",
                (*sorted(PURGEABLE_ATTEMPT_STATES), cutoff),
            )
            return int(changed.rowcount)
        finally:
            conn.close()
