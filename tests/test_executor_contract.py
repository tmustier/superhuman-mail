from __future__ import annotations

from unittest.mock import patch

import pytest

from superhuman_mail import executor


def _record() -> dict:
    return {
        "attestation_id": "sha256:" + "a" * 64,
        "account": {"email": "owner@example.test", "provider_user_id": "provider-1"},
        "thread_id": "thread-1",
        "draft_id": "draft-1",
        "history_id": 42,
        "delay_seconds": 20,
        "superhuman_id": "send-1",
        "fingerprint": {"exact": "sha256:" + "b" * 64},
        "outgoing_payload": {},
        "renderer": {},
        "screenshots": [],
    }


def test_executor_contract_is_credential_free_and_has_no_raw_fallback(monkeypatch):
    assert executor.CONTRACT["schema"] == "shm-executor/v1"
    assert executor.CONTRACT["raw_send_fallback"] is False
    assert executor.CONTRACT["send"]["definitive_pre_post_exit"] == 10
    monkeypatch.delenv("SHM_AUTH_TOKEN_STDIN", raising=False)
    with pytest.raises(executor.ExecutorContractError, match="signed credential bridge"):
        executor.require_credential_bridge()
    monkeypatch.setenv("SHM_AUTH_TOKEN_STDIN", "1")
    executor.require_credential_bridge()


def test_get_rerenders_and_returns_content_free_binding():
    record = _record()
    verified = {"fingerprint": record["fingerprint"]}
    with (
        patch("superhuman_mail.executor.attestation.load", return_value=record),
        patch("superhuman_mail.executor.attestation.verify"),
        patch("superhuman_mail.executor.attestation.revalidate_for_send", return_value=verified),
        patch("superhuman_mail.executor.approval.binding_for_attestation", return_value={"action": "superhuman.send"}),
    ):
        result = executor.get_rendered(
            "thread-1",
            "draft-1",
            account="owner@example.test",
            attestation_reference="fixture.json",
        )
    assert result["draft_fingerprint"] == record["fingerprint"]["exact"]
    assert result["approval_binding"] == {"action": "superhuman.send"}
    assert "outgoing_payload" not in result


def test_conditional_send_rejects_wrong_revision_before_rerender_or_post():
    record = _record()
    with (
        patch("superhuman_mail.executor.attestation.load", return_value=record),
        patch("superhuman_mail.executor.attestation.verify"),
        patch("superhuman_mail.executor.attestation.revalidate_for_send") as rerender,
        patch("superhuman_mail.executor.send._post_exact_payload") as post,
    ):
        with pytest.raises(executor.ExecutorContractError, match="revision"):
            executor.send_conditional(
                "thread-1",
                "draft-1",
                account="owner@example.test",
                attestation_reference="fixture.json",
                if_revision="sha256:" + "0" * 64,
                expected_draft_fingerprint=record["fingerprint"]["exact"],
                delay=20,
                wait=0,
            )
    rerender.assert_not_called()
    post.assert_not_called()


def test_conditional_send_rechecks_exact_fingerprint_before_one_post():
    record = _record()
    verified = {"fingerprint": record["fingerprint"], "outgoing_payload": {"fixture": True}}
    with (
        patch("superhuman_mail.executor.attestation.load", return_value=record),
        patch("superhuman_mail.executor.attestation.verify"),
        patch("superhuman_mail.executor.attestation.revalidate_for_send", return_value=verified),
        patch("superhuman_mail.executor.send._post_exact_payload") as post,
        patch("superhuman_mail.executor._observe_result", return_value={"provider_confirmed": True}),
    ):
        result = executor.send_conditional(
            "thread-1",
            "draft-1",
            account="owner@example.test",
            attestation_reference="fixture.json",
            if_revision=executor.revision_id(record),
            expected_draft_fingerprint=record["fingerprint"]["exact"],
            delay=20,
            wait=0,
        )
    assert result["provider_confirmed"] is True
    post.assert_called_once_with({"fixture": True}, delay=20)
