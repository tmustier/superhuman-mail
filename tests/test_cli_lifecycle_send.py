"""CLI contracts for lifecycle, strict attestation, and pending exit status."""
from __future__ import annotations

import json
from unittest.mock import ANY, patch

from superhuman_mail import send
from superhuman_mail.cli import main
from superhuman_mail._envelope import ok

THREAD = "thread_fixture"
DRAFT = "draft_fixture"
ACCOUNT = "owner@example.test"


def _send_data(state, *, sent=False):
    return {
        "state": state,
        "sent": sent,
        "provider_confirmed": sent,
        "attempt_id": "attempt_fixture",
    }


def test_send_status_subcommand_spelling_maps_to_read_only_status(capsys):
    with patch("superhuman_mail.cli._send.status", return_value=ok("send.status", _send_data("send_pending_undo"))) as status:
        code = main(["send", "status", THREAD, DRAFT, "--account", ACCOUNT, "--wait", "5"])
    assert code == 4
    status.assert_called_once_with(THREAD, DRAFT, account=ACCOUNT, wait=5.0)
    output = json.loads(capsys.readouterr().out)
    assert output["data"]["sent"] is False


def test_active_or_terminal_failure_status_exits_one(capsys):
    for state in ("active_draft", "send_failed", "send_aborted", "discarded"):
        with patch("superhuman_mail.cli._send.status", return_value=ok("send.status", _send_data(state))):
            code = main(["send", "status", THREAD, DRAFT, "--account", ACCOUNT])
        assert code == 1
        capsys.readouterr()


def test_inconsistent_possible_send_status_exits_four(capsys):
    with patch("superhuman_mail.cli._send.status", return_value=ok("send.status", _send_data("inconsistent"))):
        code = main(["send", "status", THREAD, DRAFT, "--account", ACCOUNT])
    assert code == 4
    capsys.readouterr()


def test_scheduled_status_exits_zero_without_sent_claim(capsys):
    with patch("superhuman_mail.cli._send.status", return_value=ok("send.status", _send_data("scheduled"))):
        code = main(["send", "status", THREAD, DRAFT, "--account", ACCOUNT])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["data"]["sent"] is False


def test_provider_confirmed_status_exits_zero(capsys):
    with patch("superhuman_mail.cli._send.status", return_value=ok("send.status", _send_data("sent_provider_confirmed", sent=True))):
        code = main(["send", "status", THREAD, DRAFT, "--account", ACCOUNT])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["data"]["provider_confirmed"] is True


def test_confirm_passes_attestation_approval_and_wait_and_pending_exits_four(capsys):
    result = ok("send", _send_data("sent_backend_confirmed", sent=False))
    with patch("superhuman_mail.cli._send.execute", return_value=result) as execute:
        code = main([
            "send", "--confirm", THREAD, DRAFT,
            "--account", ACCOUNT,
            "--attestation", "attestation_fixture",
            "--approval-receipt", "receipt-fixture.json",
            "--delay", "20",
            "--wait", "30",
            "--cdp-url", "http://127.0.0.1:9333",
            "--window-id", "123",
        ])
    assert code == 4
    execute.assert_called_once_with(
        THREAD,
        DRAFT,
        delay=20,
        account=ACCOUNT,
        attestation="attestation_fixture",
        approval_receipt="receipt-fixture.json",
        approval_ref=None,
        wait=30.0,
        renderer=ANY,
    )
    renderer = execute.call_args.kwargs["renderer"]
    assert renderer.cdp_url == "http://127.0.0.1:9333"
    assert renderer.window_id == 123
    assert json.loads(capsys.readouterr().out)["data"]["sent"] is False


def test_qualified_website_inbound_passes_narrow_policy_args_and_pending_exits_four(capsys):
    result = ok("send.qualified_website_inbound", {
        **_send_data("unknown", sent=False),
        "post_claimed": True,
        "automation_policy": "qualified_website_inbound_v1",
    })
    with patch("superhuman_mail.cli._send.execute_qualified_website_inbound", return_value=result) as execute:
        code = main([
            "send", "--qualified-website-inbound", THREAD, DRAFT,
            "--account", ACCOUNT,
            "--lead-email", "lead@example.test",
            "--qualification-ref", "website-inbounds:webin-0123abcd",
            "--delay", "20",
            "--wait", "30",
        ])
    assert code == 4
    execute.assert_called_once_with(
        THREAD,
        DRAFT,
        delay=20,
        account=ACCOUNT,
        lead_email="lead@example.test",
        qualification_ref="website-inbounds:webin-0123abcd",
        wait=30.0,
    )
    output = json.loads(capsys.readouterr().out)
    assert output["data"]["post_claimed"] is True


def test_executor_status_and_abort_route_to_canonical_authority(capsys):
    receipt_id = "sha256:" + "a" * 64
    with patch("superhuman_mail.cli._authority_client.status", return_value=ok("executor.status", {"state": "grace"})) as status:
        assert main(["executor", "status", receipt_id]) == 0
    status.assert_called_once_with(receipt_id)
    assert json.loads(capsys.readouterr().out)["data"]["state"] == "grace"
    with patch("superhuman_mail.cli._authority_client.abort", return_value=ok("executor.abort", {"state": "aborted"})) as abort:
        assert main(["executor", "abort", receipt_id]) == 0
    abort.assert_called_once_with(receipt_id)
    assert json.loads(capsys.readouterr().out)["data"]["state"] == "aborted"


def test_approval_verify_delegates_external_authority_and_replay_check(capsys):
    summary = {
        "authority": "external_ed25519_receipt_v1",
        "verified": True,
        "usable": True,
        "consumed": False,
    }
    with patch("superhuman_mail.cli._approval.show_safe", return_value=summary) as verify:
        code = main([
            "approval", "verify", "receipt.json",
            "--attestation", "attestation-fixture",
        ])
    assert code == 0
    verify.assert_called_once_with(
        "receipt.json",
        attestation_reference="attestation-fixture",
    )
    assert json.loads(capsys.readouterr().out)["data"]["verified"] is True


def test_draft_read_passes_lifecycle_filters(capsys):
    with patch("superhuman_mail.cli._draft.read", return_value=ok("draft.read", {"drafts": []})) as read:
        code = main(["draft", "read", THREAD, "--draft-id", DRAFT, "--account", ACCOUNT, "--active-only"])
    assert code == 0
    read.assert_called_once_with(THREAD, draft_id=DRAFT, account=ACCOUNT, active_only=True)
    capsys.readouterr()


def test_attest_render_terminal_preflight_returns_json_error_not_traceback(capsys, tmp_path):
    safety_error = send.SendSafetyError("DRAFT_ALREADY_SENT", "already sent")
    with patch("superhuman_mail.send._preflight", side_effect=safety_error):
        code = main([
            "draft", "attest-render", THREAD, DRAFT,
            "--account", ACCOUNT,
            "--output", str(tmp_path),
        ])
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "DRAFT_ALREADY_SENT"


def test_attestation_show_delegates_signature_and_binding_verification(capsys):
    summary = {
        "attestation_id": "attestation_fixture",
        "signature_valid": True,
        "expired": False,
        "usable": True,
        "summary": {"to_count": 1},
    }
    with patch("superhuman_mail.cli._attestation.show_safe", return_value=summary) as show:
        code = main([
            "attestation", "show", "attestation_fixture",
            "--account", ACCOUNT,
            "--thread-id", THREAD,
            "--draft-id", DRAFT,
        ])
    assert code == 0
    show.assert_called_once_with(
        "attestation_fixture",
        account=ACCOUNT,
        thread_id=THREAD,
        draft_id=DRAFT,
    )
    assert json.loads(capsys.readouterr().out)["data"]["usable"] is True


def test_attest_render_emits_safe_summary_not_body_or_payload(capsys, tmp_path):
    record = {
        "attestation_id": "attestation_fixture",
        "artifact_path": str(tmp_path / "record.json"),
        "created_at": "2026-07-10T12:00:00Z",
        "expires_at": "2026-07-10T12:15:00Z",
        "send_eligible": True,
        "confidence": "exact_superhuman_renderer",
        "account": {"email": ACCOUNT, "provider_user_id": "user_fixture"},
        "thread_id": THREAD,
        "draft_id": DRAFT,
        "superhuman_id": "sid.fixture",
        "fingerprint": {"exact": "sha256:fixture"},
        "renderer": {"web_version": "fixture"},
        "screenshots": [],
        "source": {"body": "PRIVATE BODY"},
        "outgoing_payload": {"html_body": "PRIVATE BODY"},
    }
    with patch("superhuman_mail.cli._attestation.create", return_value=record) as create:
        code = main([
            "draft", "attest-render", THREAD, DRAFT,
            "--account", ACCOUNT,
            "--output", str(tmp_path),
            "--cdp-url", "http://127.0.0.1:9333",
        ])
    assert code == 0
    create.assert_called_once()
    output_text = capsys.readouterr().out
    assert "PRIVATE BODY" not in output_text
    assert "user_fixture" not in output_text
    assert "sid.fixture" not in output_text
    output = json.loads(output_text)
    assert output["data"]["attestation_id"] == "attestation_fixture"
    assert output["data"]["send_eligible"] is True
