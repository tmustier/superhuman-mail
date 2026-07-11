"""Exact render attestation, signing, and stale-check tests."""
from __future__ import annotations

import copy
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from superhuman_mail import approval, attestation, lifecycle

THREAD = "thread_fixture"
DRAFT = "draft_fixture"
SID = "sid.fixture"
ACCOUNT = {"email": "owner@example.test", "provider_user_id": "user-fixture"}
VERSION = "fixture-version"


def _draft(**overrides):
    value = {
        "id": DRAFT,
        "threadId": THREAD,
        "action": "reply",
        "from": {"email": ACCOUNT["email"], "name": "Owner"},
        "to": [{"email": "recipient@example.test", "name": "Recipient"}],
        "cc": [],
        "bcc": [],
        "subject": "Fixture",
        "body": "<div>Hello</div>",
        "quotedContent": "<div>Earlier</div>",
        "quotedContentInlined": False,
        "inReplyTo": "message_earlier",
        "inReplyToRfc822Id": "<earlier@example.test>",
        "references": ["<earlier@example.test>"],
        "rfc822Id": "<draft@example.test>",
        "attachments": [],
    }
    value.update(overrides)
    return value


def _preflight(draft=None, *, history=42):
    value = draft or _draft()
    return {
        "thread_id": THREAD,
        "draft_id": DRAFT,
        "draft": value,
        "wrapper": {"draft": value},
        "outgoing": {},
        "warnings": [],
        "lifecycle": {
            "account": ACCOUNT,
            "state": lifecycle.ACTIVE,
            "observations": [{"source": "superhuman_userdata_api", "history_id": history}],
        },
    }


def _payload(html="<div>Hello</div><br><div>Signature</div>"):
    return {
        "headers": [
            {"name": "X-Mailer", "value": f"Superhuman Desktop ({VERSION})"},
            {"name": "X-Superhuman-ID", "value": SID},
            {"name": "X-Superhuman-Draft-ID", "value": DRAFT},
        ],
        "superhuman_id": SID,
        "rfc822_id": "<draft@example.test>",
        "thread_id": THREAD,
        "message_id": DRAFT,
        "in_reply_to": "message_earlier",
        "from": {"email": ACCOUNT["email"], "name": "Owner"},
        "to": [{"email": "recipient@example.test", "name": "Recipient"}],
        "cc": [],
        "bcc": [],
        "subject": "Fixture",
        "html_body": html,
        "attachments": [],
        "scheduled_for": None,
        "abort_on_reply": False,
        "current_message_ids": ["message_earlier"],
        "mail_merge_recipients": [],
        "sensitivity_label_id": None,
        "sensitivity_tenant_id": None,
    }


class FakeRenderer:
    def __init__(self, *, draft=None, payload=None, version=VERSION, dirty=False, events=None):
        self.draft = draft or _draft()
        self.payload = payload or _payload()
        self.version = version
        self.dirty = dirty
        self.events = events or []
        self.calls = []

    def probe(self, request, *, output_dir: Path):
        self.calls.append(copy.deepcopy(request))
        output_dir.mkdir(parents=True, exist_ok=True)
        compose = output_dir / "compose.png"
        outgoing = output_dir / "outgoing.png"
        compose.write_bytes(b"\x89PNG\r\n\x1a\ncompose")
        outgoing.write_bytes(b"\x89PNG\r\n\x1a\noutgoing")
        return {
            "account_email": ACCOUNT["email"],
            "thread_id": THREAD,
            "draft_id": DRAFT,
            "dirty": self.dirty,
            "live_draft_json": copy.deepcopy(self.draft),
            "editor_html": "<div>Hello</div>",
            "outgoing_payload": copy.deepcopy(self.payload),
            "signature_settings": {"signature_id": "signature-fixture"},
            "app_version": "1041.0.15",
            "web_version": self.version,
            "surface": "superhuman-desktop",
            "network_events": copy.deepcopy(self.events),
            "screenshots": [str(compose), str(outgoing)],
        }


@pytest.fixture(autouse=True)
def _key_and_version(monkeypatch, tmp_path):
    monkeypatch.setattr(attestation, "_attestation_key", lambda *, create: b"k" * 32)
    monkeypatch.setattr(attestation, "DEFAULT_ALLOWED_RENDERER_BUILDS", {("1041.0.15", VERSION)})
    monkeypatch.setenv("SHM_STATE_DIR", str(tmp_path / "state"))


def _create(tmp_path, renderer=None):
    renderer = renderer or FakeRenderer()
    with patch("superhuman_mail.send._superhuman_id", return_value=SID):
        with patch("superhuman_mail.send._preflight", side_effect=[_preflight(), _preflight()]):
            return attestation.create(
                THREAD,
                DRAFT,
                account=ACCOUNT["email"],
                output_dir=tmp_path / "preview",
                renderer=renderer,
            )


def test_bundled_renderer_declares_versioned_payload_contract():
    script = Path(attestation.__file__).with_name("renderer_probe.js")
    completed = subprocess.run(
        ["node", str(script), "--print-contract"],
        capture_output=True,
        text=True,
        check=True,
    )
    contract = json.loads(completed.stdout)
    assert contract["adapter_version"] == attestation.ADAPTER_VERSION
    assert contract["mutates_mail_state"] is False
    assert contract["blocks_non_idempotent_before_dispatch"] is True
    assert contract["network_offline_during_render"] is True
    assert {
        origin: set(routes)
        for origin, routes in contract["read_only_post_routes"].items()
    } == attestation._READ_ONLY_POST_ROUTES
    assert contract["reminder"] == "persisted_draft_only_current_build"
    assert set(contract["outgoing_fields"]) == attestation.OUTGOING_FIELDS
    assert "html_body" in contract["outgoing_fields"]
    assert "reminder" not in contract["outgoing_fields"]


def test_bundled_renderer_policy_aborts_write_before_dispatch():
    script = Path(attestation.__file__).with_name("renderer_probe.js")
    requests = [
        {"method": "POST", "url": "https://mail.superhuman.com/~backend/v3/userdata.read"},
        {"method": "POST", "url": "https://evil.example/~backend/v3/userdata.read"},
        {"method": "POST", "url": "https://evil.example/~backend/v3/sessions.getTokens"},
        {"method": "POST", "url": "https://mail.superhuman.com/~backend/v3/userdata.writeMessage"},
        {"method": "DELETE", "url": "https://example.test/anything"},
    ]
    completed = subprocess.run(
        ["node", str(script), "--test-network-policy"],
        input=json.dumps(requests),
        capture_output=True,
        text=True,
        check=True,
    )
    decisions = json.loads(completed.stdout)
    assert [item["disposition"] for item in decisions] == ["continue", "fail", "fail", "fail", "fail"]
    source = script.read_text()
    assert 'this.send("Fetch.failRequest"' in source
    assert 'client.send("Page.bringToFront"' not in source
    assert "probe will not focus or navigate" in source


def test_probe_network_policy_fails_closed_on_unknown_non_idempotent_requests():
    events = [
        {"method": "GET", "url": "https://mail.superhuman.com/image.png"},
        {"method": "POST", "url": "https://mail.superhuman.com/~backend/v3/userdata.read"},
        {"method": "POST", "url": "https://evil.example/~backend/v3/userdata.read"},
        {"method": "POST", "url": "https://mail.superhuman.com/~backend/v3/newMutation.unknown"},
        {"method": "DELETE", "url": "https://example.test/anything"},
    ]
    assert attestation._network_writes(events) == [
        {"method": "POST", "url": "https://evil.example/~backend/v3/userdata.read"},
        {"method": "POST", "url": "https://mail.superhuman.com/~backend/v3/newmutation.unknown"},
        {"method": "DELETE", "url": "https://example.test/anything"},
    ]


def test_renderer_rejects_non_loopback_cdp_endpoint(tmp_path):
    renderer = attestation.CdpRenderer(cdp_url="https://attacker.example.test:9222")
    with pytest.raises(attestation.AttestationError) as caught:
        renderer.probe({}, output_dir=tmp_path)
    assert caught.value.code == "RENDERER_ENDPOINT_UNSAFE"


def test_production_allowlist_binds_app_and_web_versions(monkeypatch):
    monkeypatch.setattr(attestation, "DEFAULT_ALLOWED_RENDERER_BUILDS", {("1041.0.15", "2026-07-09T19:06:39Z")})
    assert attestation._renderer_build_allowed("1041.0.15", "2026-07-09T19:06:39Z") is True
    assert attestation._renderer_build_allowed("1042.0.0", "2026-07-09T19:06:39Z") is False


def test_create_binds_exact_source_editor_payload_versions_and_screenshots(tmp_path):
    renderer = FakeRenderer()
    record = _create(tmp_path, renderer)
    assert record["send_eligible"] is True
    assert record["confidence"] == "exact_superhuman_renderer"
    assert record["attestation_id"].startswith("sha256:")
    assert len(record["attestation_id"]) == 71
    assert record["superhuman_id"] == SID
    assert record["fingerprint"]["fields"]["outgoing_payload"] == attestation.sha256(
        attestation.canonical_bytes(_payload())
    )
    assert len(record["screenshots"]) == 2
    assert renderer.calls[0]["superhuman_id"] == SID
    attestation.verify(record)
    loaded = attestation.load(record["attestation_id"])
    assert loaded["attestation_id"] == record["attestation_id"]


def test_safe_show_verifies_binding_and_redacts_all_mail_content(tmp_path):
    record = _create(tmp_path)
    summary = attestation.show_safe(
        record["attestation_id"],
        account=ACCOUNT["email"],
        thread_id=THREAD,
        draft_id=DRAFT,
    )
    assert summary["signature_valid"] is True
    assert summary["usable"] is True
    assert summary["binding_match"] is True
    assert summary["approval_binding"] == approval.binding_for_attestation(record)
    assert summary["summary"] == {
        "to_count": 1,
        "cc_count": 0,
        "bcc_count": 0,
        "attachment_count": 0,
        "empty_subject": False,
        "scheduled": False,
        "has_quote": True,
        "editor_normalized_changed": False,
    }
    serialized = str(summary)
    assert "Hello" not in serialized
    assert "recipient@example.test" not in serialized
    assert "Fixture" not in serialized
    assert ACCOUNT["provider_user_id"] not in serialized
    assert SID not in serialized

    with pytest.raises(attestation.AttestationError) as caught:
        attestation.show_safe(record["attestation_id"], draft_id="draft_other")
    assert caught.value.code == "ATTESTATION_BINDING_MISMATCH"


def test_safe_show_reports_valid_but_expired_as_unusable(tmp_path):
    record = _create(tmp_path)
    expired = copy.deepcopy(record)
    expired["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    expired = attestation._seal(attestation._unsigned(expired))
    attestation.save(expired)
    summary = attestation.show_safe(expired["attestation_id"])
    assert summary["signature_valid"] is True
    assert summary["expired"] is True
    assert summary["usable"] is False


@pytest.mark.parametrize("value", [2**53, float("nan"), float("inf")])
def test_nonportable_numeric_identity_values_fail_before_sealing(value):
    with pytest.raises(attestation.AttestationError) as caught:
        attestation._seal({"signature_settings": {"value": value}, "screenshots": []})
    assert caught.value.code == "ATTESTATION_NONPORTABLE"


def test_malformed_attestation_returns_typed_invalid_error():
    with pytest.raises(attestation.AttestationError) as caught:
        attestation.verify({})
    assert caught.value.code == "ATTESTATION_INVALID"


def test_tampered_screenshot_is_rejected_even_when_record_signature_is_valid(tmp_path):
    record = _create(tmp_path)
    Path(record["screenshots"][0]["path"]).write_bytes(b"tampered")
    with pytest.raises(attestation.AttestationError) as caught:
        attestation.verify(record)
    assert caught.value.code == "ATTESTATION_ARTIFACT_MISMATCH"


def test_tampered_or_expired_attestation_is_rejected(tmp_path):
    record = _create(tmp_path)
    tampered = copy.deepcopy(record)
    tampered["outgoing_payload"]["subject"] = "Changed"
    with pytest.raises(attestation.AttestationError, match="canonical content"):
        attestation.verify(tampered)

    expired = copy.deepcopy(record)
    expired["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    # Re-seal to isolate expiry behavior.
    expired = attestation._seal(attestation._unsigned(expired))
    with pytest.raises(attestation.AttestationError, match="expired"):
        attestation.verify(expired)


def test_dirty_renderer_version_mismatch_and_write_event_fail_closed(tmp_path):
    cases = [
        (FakeRenderer(dirty=True), "DIRTY_RENDERER_DRAFT"),
        (FakeRenderer(version="new-unreviewed-version"), "RENDERER_VERSION_UNSUPPORTED"),
        (FakeRenderer(events=[{"method": "POST", "url": "https://mail.superhuman.com/~backend/messages/send"}]), "RENDERER_WROTE_LIVE_STATE"),
    ]
    for index, (renderer, code) in enumerate(cases):
        with patch("superhuman_mail.send._superhuman_id", return_value=SID):
            with patch("superhuman_mail.send._preflight", return_value=_preflight()):
                with pytest.raises(attestation.AttestationError) as caught:
                    attestation.create(
                        THREAD,
                        DRAFT,
                        account=ACCOUNT["email"],
                        output_dir=tmp_path / f"case-{index}",
                        renderer=renderer,
                    )
        assert caught.value.code == code


def test_readable_attachment_bytes_are_stream_hashed():
    attached = _draft(attachments=[{
        "uuid": "attachment-fixture",
        "source": {"type": "upload-firebase", "url": "https://storage.googleapis.com/file"},
    }])

    class Response:
        def __init__(self):
            self.chunks = iter([b"abc", b"def", b""])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return next(self.chunks)

    with patch("superhuman_mail.attestation.urllib.request.urlopen", return_value=Response()):
        digests = attestation.attachment_digests(attached)
    assert digests["attachment-fixture"] == attestation.sha256(b"abcdef")


def test_attachment_bytes_reject_non_allowlisted_source_without_request():
    attached = _draft(attachments=[{
        "uuid": "attachment-fixture",
        "source": {"type": "upload-firebase", "url": "https://attacker.example.test/file"},
    }])
    with patch("superhuman_mail.attestation.urllib.request.urlopen") as urlopen:
        with pytest.raises(attestation.AttestationError) as caught:
            attestation.attachment_digests(attached)
    assert caught.value.code == "UNATTESTABLE_ATTACHMENT"
    urlopen.assert_not_called()


def test_unreadable_attachment_is_not_send_eligible(tmp_path):
    attached = _draft(attachments=[{
        "uuid": "attachment-fixture",
        "name": "file.pdf",
        "type": "application/pdf",
        "size": 10,
        "source": {"type": "remote-without-digest"},
    }])
    with patch("superhuman_mail.send._superhuman_id", return_value=SID):
        with patch("superhuman_mail.send._preflight", return_value=_preflight(attached)):
            with pytest.raises(attestation.AttestationError) as caught:
                attestation.create(
                    THREAD,
                    DRAFT,
                    account=ACCOUNT["email"],
                    output_dir=tmp_path / "attachment",
                    renderer=FakeRenderer(draft=attached),
                )
    assert caught.value.code == "UNATTESTABLE_ATTACHMENT"


def test_send_time_second_probe_returns_fresh_exact_payload(tmp_path):
    record = _create(tmp_path)
    renderer = FakeRenderer()
    with patch("superhuman_mail.send._preflight", side_effect=[_preflight(), _preflight()]):
        verified = attestation.revalidate_for_send(
            record,
            account=ACCOUNT["email"],
            renderer=renderer,
            output_dir=tmp_path / "send-time",
        )
    assert verified["outgoing_payload"] == _payload()
    assert verified["outgoing_payload_bytes"] == attestation.canonical_bytes(_payload())
    assert renderer.calls[0]["superhuman_id"] == SID


def test_stale_source_blocks_before_second_probe(tmp_path):
    record = _create(tmp_path)
    changed = _preflight(_draft(to=[{"email": "different@example.test"}]))
    renderer = FakeRenderer()
    with patch("superhuman_mail.send._preflight", return_value=changed):
        with pytest.raises(attestation.AttestationError) as caught:
            attestation.revalidate_for_send(record, account=ACCOUNT["email"], renderer=renderer)
    assert caught.value.code == "STALE_ATTESTATION"
    assert renderer.calls == []


def test_executor_prepared_record_rerenders_without_worker_hmac(monkeypatch, tmp_path):
    state = tmp_path / "state"
    prepared = state / "prepared-renders" / "render-fixture"
    prepared.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("SHM_EXECUTOR_PREPARE_MODE", "1")
    with patch("superhuman_mail.send._superhuman_id", return_value=SID):
        with patch("superhuman_mail.send._preflight", side_effect=[_preflight(), _preflight()]):
            record = attestation.create(
                THREAD, DRAFT, account=ACCOUNT["email"], output_dir=prepared, renderer=FakeRenderer(),
            )
    assert record["signature"] == "executor-prepared:v1"
    marker_root = state / "trusted-prepared"
    marker_root.mkdir(mode=0o700)
    marker = marker_root / f"{record['attestation_id'][7:]}.json"
    marker.write_text(json.dumps({"schema": "shm-trusted-prepared/v1", "attestation_id": record["attestation_id"]}))
    marker.chmod(0o600)
    monkeypatch.delenv("SHM_EXECUTOR_PREPARE_MODE")
    monkeypatch.setenv("SHM_EXECUTOR_TRUSTED_PREPARED_DIR", str(marker_root))
    monkeypatch.setattr(attestation, "_attestation_key", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("HMAC must not be read")))
    with patch("superhuman_mail.send._preflight", side_effect=[_preflight(), _preflight()]):
        verified = attestation.revalidate_for_send(
            record, account=ACCOUNT["email"], renderer=FakeRenderer(), output_dir=state / "send-time",
        )
    assert verified["fingerprint"]["exact"] == record["fingerprint"]["exact"]
    marker.unlink()
    with pytest.raises(attestation.AttestationError) as caught:
        attestation.verify_prepared(record, marker_root=marker_root)
    assert caught.value.code == "ATTESTATION_PREPARED_INVALID"


def test_renderer_payload_drift_after_approval_blocks(tmp_path):
    record = _create(tmp_path)
    renderer = FakeRenderer(payload=_payload(html="<div>Changed transport bytes</div>"))
    with patch("superhuman_mail.send._preflight", side_effect=[_preflight(), _preflight()]):
        with pytest.raises(attestation.AttestationError) as caught:
            attestation.revalidate_for_send(
                record,
                account=ACCOUNT["email"],
                renderer=renderer,
                output_dir=tmp_path / "drift",
            )
    assert caught.value.code == "STALE_ATTESTATION"
    assert "outgoing_payload" in caught.value.hint
