"""Strict send attempt/reconciliation tests with no live transport."""
from __future__ import annotations

import concurrent.futures
import threading
import urllib.error
from unittest.mock import patch

from superhuman_mail import attestation, lifecycle, send
from superhuman_mail.attempts import AttemptJournal

THREAD = "thread_fixture"
DRAFT = "draft_fixture"
SID = "sid.fixture"
ACCOUNT = {"email": "owner@example.test", "provider_user_id": "user-fixture"}


def _record():
    return {
        "attestation_id": "attestation_fixture",
        "thread_id": THREAD,
        "draft_id": DRAFT,
        "delay_seconds": 20,
        "superhuman_id": SID,
        "account": ACCOUNT,
        "fingerprint": {"exact": "sha256:approved"},
    }


def _state(name, *, provider_id=None):
    return {
        "account": ACCOUNT,
        "thread_id": THREAD,
        "draft_id": DRAFT,
        "state": name,
        "terminal": name in lifecycle.TERMINAL_STATES,
        "send_blocked": name in lifecycle.BLOCKING_STATES,
        "outbound_evidence": name == lifecycle.PROVIDER_CONFIRMED,
        "confidence": "fixture",
        "consistency": "matched",
        "timestamps": {
            "sent_at": "2026-07-10T12:00:00Z" if name in {lifecycle.BACKEND_CONFIRMED, lifecycle.PROVIDER_CONFIRMED} else None,
            "provider_message_at": "2026-07-10T12:00:01Z" if name == lifecycle.PROVIDER_CONFIRMED else None,
        },
        "provider_message": {"id": provider_id} if provider_id else None,
    }


def _observe(state):
    return state, {"draft": {}}, []


def _preflight():
    return {
        "draft": {"id": DRAFT, "threadId": THREAD},
        "lifecycle": {"account": ACCOUNT},
        "warnings": [],
    }


def _run(journal, state, *, post_side_effect=None, wait=0):
    with patch("superhuman_mail.send._preflight", return_value=_preflight()):
        with patch("superhuman_mail.send._attestation.load", return_value=_record()):
            with patch("superhuman_mail.send._attestation.verify"):
                with patch("superhuman_mail.send._attestation.revalidate_for_send", return_value={"outgoing_payload": {"fixture": True}}):
                    with patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
                        with patch("superhuman_mail.send.lifecycle.observe", return_value=_observe(state)):
                            with patch("superhuman_mail.send._post_exact_payload", side_effect=post_side_effect) as post:
                                result = send.execute(
                                    THREAD,
                                    DRAFT,
                                    account=ACCOUNT["email"],
                                    attestation="attestation_fixture",
                                    approval_ref="approval_fixture",
                                    wait=wait,
                                    journal=journal,
                                )
    return result, post


def test_transport_posts_canonical_fresh_payload_bytes_only():
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{}"

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    payload = {"html_body": "<p>exact</p>", "superhuman_id": SID, "headers": []}
    with patch("superhuman_mail.send._auth.api_headers", return_value={"Authorization": "fixture"}):
        with patch("superhuman_mail.send.urllib.request.urlopen", side_effect=urlopen):
            send._post_exact_payload(payload, delay=20)
    request = captured["request"]
    expected = {"version": 3, "outgoing_message": payload, "delay": 20, "is_multi_recipient": True}
    assert request.method == "POST"
    assert request.data == attestation.canonical_bytes(expected)
    assert captured["timeout"] == 30


def test_stale_second_probe_never_creates_or_strands_attempt(tmp_path):
    from superhuman_mail.attestation import AttestationError

    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    with patch("superhuman_mail.send._preflight", return_value=_preflight()):
        with patch("superhuman_mail.send._attestation.load", return_value=_record()):
            with patch("superhuman_mail.send._attestation.verify"):
                with patch(
                    "superhuman_mail.send._attestation.revalidate_for_send",
                    side_effect=AttestationError("STALE_ATTESTATION", "changed"),
                ):
                    with patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
                        with patch("superhuman_mail.send._post_exact_payload") as post:
                            result = send.execute(
                                THREAD,
                                DRAFT,
                                account=ACCOUNT["email"],
                                attestation="attestation_fixture",
                                approval_ref="approval_fixture",
                                wait=0,
                                journal=journal,
                            )
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "STALE_ATTESTATION"
    assert journal.get(ACCOUNT["provider_user_id"], DRAFT) is None
    post.assert_not_called()


def test_http_acceptance_during_undo_window_is_pending_not_sent(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    result, post = _run(journal, _state(lifecycle.PENDING_UNDO))
    post.assert_called_once_with({"fixture": True}, delay=20)
    assert result["status"] == "succeeded"
    assert result["data"]["state"] == lifecycle.PENDING_UNDO
    assert result["data"]["accepted"] is True
    assert result["data"]["sent"] is False
    assert result["data"]["provider_confirmed"] is False
    assert result["data"]["approval_authority"] == "correlation_only"
    assert result["data"]["approval_verified"] is False
    assert result["data"]["unattended_send_eligible"] is False
    assert result["data"]["idempotency_scope"] == "local_cooperating_processes"


def test_provider_confirmation_is_only_sent_success(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    result, post = _run(journal, _state(lifecycle.PROVIDER_CONFIRMED, provider_id="message_fixture"))
    assert post.call_count == 1
    assert result["status"] == "succeeded"
    assert result["data"]["state"] == lifecycle.PROVIDER_CONFIRMED
    assert result["data"]["sent"] is True
    assert result["data"]["provider_confirmed"] is True
    assert result["data"]["provider_message_id"] == "message_fixture"


def test_retry_reconciles_existing_attempt_without_second_probe_or_post(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    state = _state(lifecycle.PROVIDER_CONFIRMED, provider_id="message_fixture")
    result, first_post = _run(journal, state)
    assert result["data"]["sent"] is True
    assert first_post.call_count == 1

    with patch("superhuman_mail.send._preflight") as preflight:
        with patch("superhuman_mail.send._attestation.load", return_value=_record()):
            with patch("superhuman_mail.send._attestation.verify"):
                with patch("superhuman_mail.send._attestation.revalidate_for_send") as probe:
                    with patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
                        with patch("superhuman_mail.send.lifecycle.observe", return_value=_observe(state)):
                            with patch("superhuman_mail.send._post_exact_payload") as second_post:
                                retried = send.execute(
                                    THREAD,
                                    DRAFT,
                                    account=ACCOUNT["email"],
                                    attestation="attestation_fixture",
                                    approval_ref="approval_fixture",
                                    wait=0,
                                    journal=journal,
                                )
    assert retried["data"]["sent"] is True
    second_post.assert_not_called()
    probe.assert_not_called()
    preflight.assert_not_called()
    assert journal.get(ACCOUNT["provider_user_id"], DRAFT)["post_count"] == 1


def test_recorded_provider_confirmation_is_not_downgraded_by_stale_cache(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    confirmed = _state(lifecycle.PROVIDER_CONFIRMED, provider_id="message_fixture")
    first, _post = _run(journal, confirmed)
    assert first["data"]["sent"] is True

    with patch("superhuman_mail.send._preflight") as preflight:
        with patch("superhuman_mail.send._attestation.load", return_value=_record()):
            with patch("superhuman_mail.send._attestation.verify"):
                with patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
                    with patch("superhuman_mail.send.lifecycle.observe", return_value=_observe(_state(lifecycle.ACTIVE))):
                        with patch("superhuman_mail.send._post_exact_payload") as post:
                            result = send.execute(
                                THREAD,
                                DRAFT,
                                account=ACCOUNT["email"],
                                attestation="attestation_fixture",
                                approval_ref="approval_fixture",
                                wait=0,
                                journal=journal,
                            )
    assert result["status"] == "succeeded"
    assert result["data"]["sent"] is True
    assert result["data"]["state"] == lifecycle.PROVIDER_CONFIRMED
    assert result["data"]["lifecycle"]["consistency"] == "observation_lag"
    preflight.assert_not_called()
    post.assert_not_called()


def test_lost_http_response_reconciles_provider_and_never_reposts(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    result, post = _run(
        journal,
        _state(lifecycle.PROVIDER_CONFIRMED, provider_id="message_fixture"),
        post_side_effect=urllib.error.URLError("connection reset"),
    )
    assert post.call_count == 1
    assert result["status"] == "succeeded"
    assert result["data"]["sent"] is True
    assert result["data"]["accepted"] is True
    stored = journal.get(ACCOUNT["provider_user_id"], DRAFT)
    assert stored["post_count"] == 1
    assert stored["response_class"] == "UNREACHABLE"


def test_unknown_outcome_remains_non_sent_and_cannot_claim_again(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    result, post = _run(journal, _state(lifecycle.ACTIVE), post_side_effect=urllib.error.URLError("reset"))
    assert post.call_count == 1
    assert result["status"] == "succeeded"
    assert result["data"]["sent"] is False
    assert result["data"]["state"] == "unknown"
    assert result["data"]["post_claimed"] is True
    assert result["data"]["accepted"] is False
    stored = journal.get(ACCOUNT["provider_user_id"], DRAFT)
    assert stored["state"] == "unknown"
    _row, claimed = journal.claim_post(stored["attempt_id"])
    assert claimed is False


def test_status_does_not_brick_never_posted_prepared_attempt(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    prepared, _created = journal.create_or_get(
        account_id=ACCOUNT["provider_user_id"],
        account_hash="account-hash",
        thread_id=THREAD,
        draft_id=DRAFT,
        attestation_id="attestation_fixture",
        approval_ref="approval_fixture",
        superhuman_id=SID,
        outgoing_fingerprint="sha256:approved",
    )
    with patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
        with patch("superhuman_mail.send.lifecycle.observe", return_value=_observe(_state(lifecycle.ACTIVE))):
            status = send.status(THREAD, DRAFT, account=ACCOUNT["email"], wait=120, journal=journal)
    stored = journal.get_by_id(prepared["attempt_id"])
    assert status["data"]["accepted"] is False
    assert status["data"]["post_claimed"] is False
    assert stored["state"] == "prepared"
    assert stored["post_count"] == 0

    result, post = _run(
        journal,
        _state(lifecycle.PROVIDER_CONFIRMED, provider_id="message_fixture"),
    )
    post.assert_called_once_with({"fixture": True}, delay=20)
    assert result["data"]["sent"] is True


def test_concurrent_prepared_rotation_surfaces_attempt_conflict_not_key_error(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    journal.create_or_get(
        account_id=ACCOUNT["provider_user_id"],
        account_hash="account-hash",
        thread_id=THREAD,
        draft_id=DRAFT,
        attestation_id="attestation_fixture",
        approval_ref="approval_fixture",
        superhuman_id=SID,
        outgoing_fingerprint="sha256:approved",
    )
    with patch("superhuman_mail.send._attestation.load", return_value=_record()):
        with patch("superhuman_mail.send._attestation.verify"):
            with patch("superhuman_mail.send._attestation.revalidate_for_send", return_value={"outgoing_payload": {"fixture": True}}):
                with patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
                    with patch.object(journal, "claim_post", side_effect=KeyError("rotated")):
                        with patch("superhuman_mail.send._post_exact_payload") as post:
                            result = send.execute(
                                THREAD,
                                DRAFT,
                                account=ACCOUNT["email"],
                                attestation="attestation_fixture",
                                approval_ref="approval_fixture",
                                journal=journal,
                            )
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "ATTEMPT_CONFLICT"
    post.assert_not_called()


def test_status_rejects_thread_mismatch_for_existing_attempt(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    journal.create_or_get(
        account_id=ACCOUNT["provider_user_id"],
        account_hash="account-hash",
        thread_id=THREAD,
        draft_id=DRAFT,
        attestation_id="attestation_fixture",
        approval_ref="approval_fixture",
        superhuman_id=SID,
        outgoing_fingerprint="sha256:approved",
    )
    with patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
        with patch("superhuman_mail.send.lifecycle.observe") as observe:
            result = send.status("wrong-thread", DRAFT, account=ACCOUNT["email"], journal=journal)
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "ATTEMPT_THREAD_MISMATCH"
    observe.assert_not_called()


def test_status_without_local_attempt_waits_for_native_job_provider_confirmation(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    states = [
        _observe(_state(lifecycle.PENDING_UNDO)),
        _observe(_state(lifecycle.BACKEND_CONFIRMED)),
        _observe(_state(lifecycle.PROVIDER_CONFIRMED, provider_id="message_fixture")),
    ]
    with patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
        with patch("superhuman_mail.send.lifecycle.observe", side_effect=states) as observe:
            with patch("superhuman_mail.send.time.monotonic", return_value=0):
                with patch("superhuman_mail.send.time.sleep"):
                    result = send.status(
                        THREAD,
                        DRAFT,
                        account=ACCOUNT["email"],
                        wait=120,
                        journal=journal,
                    )
    assert observe.call_count == 3
    assert result["status"] == "succeeded"
    assert result["data"]["sent"] is True
    assert result["data"]["provider_message_id"] == "message_fixture"


def test_future_scheduled_job_is_success_without_sent_claim(tmp_path):
    journal = AttemptJournal(tmp_path / "attempts.sqlite3")
    result, _post = _run(journal, _state(lifecycle.SCHEDULED))
    assert result["status"] == "succeeded"
    assert result["data"]["state"] == lifecycle.SCHEDULED
    assert result["data"]["sent"] is False


def test_concurrent_local_callers_share_one_post_claim(tmp_path):
    journal_path = tmp_path / "attempts.sqlite3"
    counter = 0
    lock = threading.Lock()

    def post(_payload, *, delay):
        nonlocal counter
        with lock:
            counter += 1

    def call():
        journal = AttemptJournal(journal_path)
        return send.execute(
            THREAD,
            DRAFT,
            account=ACCOUNT["email"],
            attestation="attestation_fixture",
            approval_ref="approval_fixture",
            wait=0,
            journal=journal,
        )

    # Patch once around both workers so the synthetic transport remains stable
    # while SQLite arbitrates the real cross-connection claim.
    with patch("superhuman_mail.send._preflight", return_value=_preflight()):
        with patch("superhuman_mail.send._attestation.load", return_value=_record()):
            with patch("superhuman_mail.send._attestation.verify"):
                with patch("superhuman_mail.send._attestation.revalidate_for_send", return_value={"outgoing_payload": {"fixture": True}}):
                    with patch("superhuman_mail.send.lifecycle.resolve_account", return_value=(ACCOUNT, [])):
                        with patch("superhuman_mail.send.lifecycle.observe", return_value=_observe(_state(lifecycle.PROVIDER_CONFIRMED, provider_id="message_fixture"))):
                            with patch("superhuman_mail.send._post_exact_payload", side_effect=post):
                                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                                    results = list(pool.map(lambda _index: call(), range(2)))
    assert counter == 1
    assert all(result["status"] == "succeeded" for result in results)
    assert journal_path.exists()
    assert AttemptJournal(journal_path).get(ACCOUNT["provider_user_id"], DRAFT)["post_count"] == 1
