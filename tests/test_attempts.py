"""Local attempt identity, locking, and retry-safety tests."""
from __future__ import annotations

import concurrent.futures
import stat

import pytest

from superhuman_mail.attempts import AttemptConflict, AttemptJournal, IDEMPOTENCY_SCOPE


def _create(journal: AttemptJournal, **overrides):
    kwargs = {
        "account_id": "user-fixture",
        "account_hash": "hmac:account",
        "thread_id": "thread-fixture",
        "draft_id": "draft-fixture",
        "attestation_id": "attestation-fixture",
        "approval_ref": "approval-fixture",
        "superhuman_id": "sid.fixture",
        "outgoing_fingerprint": "sha256:payload",
    }
    kwargs.update(overrides)
    return journal.create_or_get(**kwargs)


def test_create_or_get_reuses_one_attempt_identity(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    first, created = _create(journal)
    second, created_again = _create(journal)
    assert created is True
    assert created_again is False
    assert first["attempt_id"] == second["attempt_id"]
    assert first["superhuman_id"] == second["superhuman_id"] == "sid.fixture"
    assert first["post_count"] == 0
    assert IDEMPOTENCY_SCOPE == "local_cooperating_processes"


def test_unclaimed_prepared_attempt_can_rotate_to_fresh_attestation(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    original, _ = _create(journal)
    replacement, created = _create(
        journal,
        attestation_id="attestation-fresh",
        approval_ref="approval-fresh",
        superhuman_id="sid.fresh",
        outgoing_fingerprint="sha256:fresh",
    )
    assert created is True
    assert replacement["attempt_id"] != original["attempt_id"]
    assert replacement["post_count"] == 0
    assert journal.get_by_id(original["attempt_id"]) is None


def test_claimed_attempt_rejects_new_identity_or_payload(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    attempt, _ = _create(journal)
    journal.claim_post(attempt["attempt_id"])
    with pytest.raises(AttemptConflict, match="superhuman_id"):
        _create(journal, superhuman_id="sid.different")
    with pytest.raises(AttemptConflict, match="outgoing_fingerprint"):
        _create(journal, outgoing_fingerprint="sha256:different")


def test_atomic_claim_allows_exactly_one_local_poster(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    attempt, _ = _create(journal)

    def claim():
        local = AttemptJournal(journal.path)
        row, won = local.claim_post(attempt["attempt_id"])
        return row["attempt_id"], won

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: claim(), range(16)))
    assert sum(int(won) for _attempt_id, won in results) == 1
    stored = journal.get_by_id(attempt["attempt_id"])
    assert stored["state"] == "posting"
    assert stored["post_count"] == 1


def test_post_claim_is_not_automatically_reopened_after_unknown_outcome(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    attempt, _ = _create(journal)
    _row, won = journal.claim_post(attempt["attempt_id"])
    assert won is True
    journal.update(attempt["attempt_id"], state="unknown", last_error="connection reset")
    _row, won_again = journal.claim_post(attempt["attempt_id"])
    assert won_again is False
    assert journal.get_by_id(attempt["attempt_id"])["post_count"] == 1


def test_journal_does_not_store_raw_approval_reference(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    attempt, _ = _create(journal, approval_ref="private approval text")
    assert attempt["approval_ref_hash"].startswith("sha256:")
    assert "private approval text" not in str(attempt)


def test_retention_never_purges_future_scheduled_attempts(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    attempt, _ = _create(journal)
    journal.update(attempt["attempt_id"], state="scheduled")
    conn = journal._connect()
    try:
        conn.execute(
            "UPDATE attempts SET updated_at = '2000-01-01T00:00:00Z' WHERE attempt_id = ?",
            (attempt["attempt_id"],),
        )
    finally:
        conn.close()
    assert journal.purge_terminal(retention_days=30) == 0
    assert journal.get_by_id(attempt["attempt_id"])["state"] == "scheduled"


def test_state_directory_and_database_are_private(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    journal = AttemptJournal(state / "attempts.sqlite3")
    _create(journal)
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE(journal.path.stat().st_mode) == 0o600
