"""Exact Superhuman renderer attestation and send-time stale verification."""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Protocol

from ._state import private_file, state_dir

SCHEMA_VERSION = 1
ADAPTER_VERSION = "superhuman-cdp-v1"
DEFAULT_TTL_SECONDS = 15 * 60
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_ALLOWED_RENDERER_BUILDS = {("1041.0.15", "2026-07-09T19:06:39Z")}
_WRITE_URL_MARKERS = (
    "/messages/send",
    "userdata.writemessage",
    "attachments.upload",
    "comments.",
    "cancel",
    "postpone",
    "unschedule",
)


class AttestationError(RuntimeError):
    """An exact render could not be safely attested or verified."""

    def __init__(self, code: str, hint: str) -> None:
        super().__init__(hint)
        self.code = code
        self.hint = hint


class Renderer(Protocol):
    def probe(self, request: dict[str, Any], *, output_dir: Path) -> dict[str, Any]: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(value: bytes | str) -> str:
    data = value.encode() if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _normalize_contact(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        email = str(value.get("email") or value.get("emailAddress") or "").strip().lower()
        return {"email": email}
    _name, email = parseaddr(str(value or "").strip())
    return {"email": (email or str(value or "")).strip().lower()}


def _attachment_metadata(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("source") or {}
    return {
        "uuid": item.get("uuid"),
        "cid": item.get("cid"),
        "name": item.get("name"),
        "type": item.get("type"),
        "size": item.get("size"),
        "inline": bool(item.get("inline")),
        "source": {
            "type": source.get("type"),
            "thread_id": source.get("threadId") or source.get("thread_id"),
            "message_id": source.get("messageId") or source.get("message_id"),
            "attachment_id": source.get("attachmentId") or source.get("attachment_id"),
            "fixed_part_id": source.get("fixedPartId") or source.get("fixed_part_id"),
            "uuid": source.get("uuid"),
            "cid": source.get("cid"),
        },
    }


def source_fields(draft: dict[str, Any], *, attachment_digests: dict[str, str] | None = None) -> dict[str, Any]:
    """Canonical send-affecting source fields, excluding observational clocks."""
    digests = attachment_digests or {}
    attachments = []
    for item in sorted((draft.get("attachments") or []), key=lambda value: str(value.get("uuid") or "")):
        metadata = _attachment_metadata(item)
        metadata["digest"] = digests.get(str(item.get("uuid") or ""))
        attachments.append(metadata)
    return {
        "id": str(draft.get("id") or ""),
        "thread_id": str(draft.get("threadId") or draft.get("thread_id") or ""),
        "action": draft.get("action"),
        "from": _normalize_contact(draft.get("from")),
        "to": [_normalize_contact(value) for value in (draft.get("to") or [])],
        "cc": [_normalize_contact(value) for value in (draft.get("cc") or [])],
        "bcc": [_normalize_contact(value) for value in (draft.get("bcc") or [])],
        "subject": str(draft.get("subject") or ""),
        "body": str(draft.get("htmlBody") or draft.get("body") or ""),
        "quoted_content": str(draft.get("quotedContent") or ""),
        "quoted_content_inlined": bool(draft.get("quotedContentInlined")),
        "in_reply_to": draft.get("inReplyTo"),
        "in_reply_to_rfc822_id": draft.get("inReplyToRfc822Id"),
        "references": list(draft.get("references") or []),
        "rfc822_id": draft.get("rfc822Id"),
        "scheduled_for": draft.get("scheduledFor"),
        "abort_on_reply": bool(draft.get("abortOnReply")),
        "reminder": draft.get("reminder"),
        "sensitivity_label_id": draft.get("sensitivityLabelId"),
        "sensitivity_tenant_id": draft.get("sensitivityTenantId"),
        "attachments": attachments,
    }


def _digest_from_metadata(attachment: dict[str, Any]) -> str | None:
    for key in ("sha256", "contentSha256", "contentHash", "digest"):
        value = attachment.get(key)
        if value:
            text = str(value)
            return text if ":" in text else f"provider:{text}"
    source = attachment.get("source") or {}
    for key in ("sha256", "contentSha256", "contentHash", "digest"):
        value = source.get(key)
        if value:
            text = str(value)
            return text if ":" in text else f"provider:{text}"
    return None


def attachment_digests(draft: dict[str, Any]) -> dict[str, str]:
    """Obtain re-verifiable bytes/digests or fail closed."""
    results: dict[str, str] = {}
    for attachment in draft.get("attachments") or []:
        identifier = str(attachment.get("uuid") or "")
        if not identifier:
            raise AttestationError("UNATTESTABLE_ATTACHMENT", "Attachment has no stable UUID")
        digest = _digest_from_metadata(attachment)
        if digest:
            results[identifier] = digest
            continue
        source = attachment.get("source") or {}
        url = source.get("url") or attachment.get("downloadUrl")
        if not url:
            raise AttestationError(
                "UNATTESTABLE_ATTACHMENT",
                f"Attachment {identifier} has neither a stable provider digest nor readable bytes",
            )
        try:
            request = urllib.request.Request(str(url), method="GET")
            digest_hash = hashlib.sha256()
            with urllib.request.urlopen(request, timeout=30) as response:
                while chunk := response.read(1024 * 1024):
                    digest_hash.update(chunk)
            results[identifier] = "sha256:" + digest_hash.hexdigest()
        except Exception as exc:
            raise AttestationError(
                "UNATTESTABLE_ATTACHMENT",
                f"Could not read bytes for attachment {identifier}: {type(exc).__name__}",
            ) from exc
    return results


def _renderer_build_allowed(app_version: str, web_version: str) -> bool:
    raw_builds = os.environ.get("SHM_RENDERER_ALLOW_BUILDS")
    if raw_builds:
        allowed: set[tuple[str, str]] = set()
        for item in raw_builds.split(","):
            app, separator, web = item.strip().partition("@")
            if separator and app and web:
                allowed.add((app, web))
        return (app_version, web_version) in allowed
    # Web-only override is retained for deterministic fixture tests and must be
    # an explicit operator choice; production defaults bind both versions.
    raw_versions = os.environ.get("SHM_RENDERER_ALLOW_VERSIONS")
    if raw_versions:
        return web_version in {item.strip() for item in raw_versions.split(",") if item.strip()}
    return (app_version, web_version) in DEFAULT_ALLOWED_RENDERER_BUILDS


def _network_writes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocked = []
    for event in events:
        method = str(event.get("method") or "GET").upper()
        url = str(event.get("url") or "").lower()
        if method not in {"GET", "HEAD", "OPTIONS"} and any(marker in url for marker in _WRITE_URL_MARKERS):
            blocked.append({"method": method, "url": url})
    return blocked


class CdpRenderer:
    """Invoke the bundled, version-gated read-only Superhuman CDP probe."""

    def __init__(
        self,
        *,
        cdp_url: str | None = None,
        window_id: int | str | None = None,
        timeout: int = 60,
    ) -> None:
        self.cdp_url = cdp_url or os.environ.get("SHM_RENDERER_CDP_URL") or DEFAULT_CDP_URL
        self.window_id = window_id or os.environ.get("SHM_RENDERER_WINDOW_ID")
        self.timeout = timeout
        self.script = Path(__file__).with_name("renderer_probe.js")

    def probe(self, request: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            output_dir.chmod(0o700)
        except OSError:
            pass
        command = ["node", str(self.script), "--cdp", self.cdp_url, "--output", str(output_dir)]
        probe_request = {**request}
        if self.window_id is not None:
            probe_request["window_id"] = str(self.window_id)
        try:
            completed = subprocess.run(
                command,
                input=canonical_bytes(probe_request),
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AttestationError("RENDERER_UNAVAILABLE", f"Could not run exact renderer probe: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode(errors="replace").strip()[:500]
            raise AttestationError("RENDERER_FAILED", detail or "Exact renderer probe failed")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AttestationError("RENDERER_FAILED", "Exact renderer probe returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise AttestationError("RENDERER_FAILED", "Exact renderer probe returned a non-object")
        return value


def _attestation_key(*, create: bool) -> bytes:
    env = os.environ.get("SHM_ATTESTATION_KEY")
    if env:
        try:
            key = base64.urlsafe_b64decode(env.encode())
        except Exception as exc:
            raise AttestationError("ATTESTATION_KEY_INVALID", "SHM_ATTESTATION_KEY is not urlsafe base64") from exc
        if len(key) < 32:
            raise AttestationError("ATTESTATION_KEY_INVALID", "Attestation HMAC key must contain at least 32 bytes")
        return key

    service = "superhuman-mail-attestation-v1"
    user = getpass.getuser()
    found = subprocess.run(
        ["security", "find-generic-password", "-a", user, "-s", service, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if found.returncode == 0 and found.stdout.strip():
        try:
            key = base64.urlsafe_b64decode(found.stdout.strip().encode())
        except Exception as exc:
            raise AttestationError("ATTESTATION_KEY_INVALID", "Keychain attestation key is invalid") from exc
        if len(key) < 32:
            raise AttestationError("ATTESTATION_KEY_INVALID", "Keychain attestation key is too short")
        return key
    if not create:
        raise AttestationError("ATTESTATION_KEY_MISSING", "Local attestation key is missing from Keychain")
    key = secrets.token_bytes(32)
    encoded = base64.urlsafe_b64encode(key).decode()
    added = subprocess.run(
        ["security", "add-generic-password", "-U", "-a", user, "-s", service, "-w", encoded],
        capture_output=True,
        text=True,
        check=False,
    )
    if added.returncode != 0:
        raise AttestationError("ATTESTATION_KEY_MISSING", "Could not create local attestation key in Keychain")
    return key


def _unsigned(record: dict[str, Any]) -> dict[str, Any]:
    # artifact_path is local presentation metadata added after sealing.
    return {
        key: value
        for key, value in record.items()
        if key not in {"attestation_id", "signature", "artifact_path"}
    }


def _seal(record: dict[str, Any]) -> dict[str, Any]:
    unsigned = _unsigned(record)
    attestation_id = hashlib.sha256(canonical_bytes(unsigned)).hexdigest()
    with_id = {**unsigned, "attestation_id": attestation_id}
    signature = hmac.new(_attestation_key(create=True), canonical_bytes(with_id), hashlib.sha256).hexdigest()
    return {**with_id, "signature": f"hmac-sha256:{signature}"}


def verify(record: dict[str, Any], *, require_unexpired: bool = True) -> None:
    expected_id = hashlib.sha256(canonical_bytes(_unsigned(record))).hexdigest()
    if not hmac.compare_digest(str(record.get("attestation_id") or ""), expected_id):
        raise AttestationError("ATTESTATION_TAMPERED", "Attestation ID does not match its canonical content")
    signature = str(record.get("signature") or "")
    expected_signature = "hmac-sha256:" + hmac.new(
        _attestation_key(create=False),
        canonical_bytes({**_unsigned(record), "attestation_id": expected_id}),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise AttestationError("ATTESTATION_TAMPERED", "Attestation signature is invalid")
    for screenshot in record.get("screenshots") or []:
        path = Path(str(screenshot.get("path") or ""))
        expected_hash = str(screenshot.get("sha256") or "")
        if not path.is_file() or not expected_hash or not hmac.compare_digest(sha256(path.read_bytes()), expected_hash):
            raise AttestationError(
                "ATTESTATION_ARTIFACT_MISMATCH",
                "An attested screenshot is missing or differs from its signed hash",
            )
    if require_unexpired and _parse_iso(str(record["expires_at"])) <= _now():
        raise AttestationError("ATTESTATION_EXPIRED", "Render attestation has expired; preview and approve again")
    if not record.get("send_eligible"):
        raise AttestationError("ATTESTATION_NOT_SEND_ELIGIBLE", "Attestation was not marked send-eligible")


def _artifact_dir() -> Path:
    path = state_dir() / "attestations"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def save(record: dict[str, Any], *, output_dir: Path | None = None) -> Path:
    attestation_id = str(record["attestation_id"])
    canonical_path = _artifact_dir() / f"{attestation_id}.json"
    canonical_path.write_bytes(canonical_bytes(record) + b"\n")
    private_file(canonical_path)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            output_dir.chmod(0o700)
        except OSError:
            pass
        copy_path = output_dir / "attestation.json"
        copy_path.write_bytes(canonical_bytes(record) + b"\n")
        private_file(copy_path)
    return canonical_path


def load(reference: str | Path) -> dict[str, Any]:
    candidate = Path(reference).expanduser()
    if not candidate.exists():
        candidate = _artifact_dir() / f"{reference}.json"
    if not candidate.exists():
        raise AttestationError("ATTESTATION_NOT_FOUND", f"Attestation not found: {reference}")
    try:
        record = json.loads(candidate.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AttestationError("ATTESTATION_INVALID", f"Cannot read attestation: {reference}") from exc
    if not isinstance(record, dict):
        raise AttestationError("ATTESTATION_INVALID", "Attestation must be a JSON object")
    return record


def show_safe(
    reference: str | Path,
    *,
    account: str | None = None,
    thread_id: str | None = None,
    draft_id: str | None = None,
) -> dict[str, Any]:
    """Verify and return a deliberately content-free local summary."""
    record = load(reference)
    verify(record, require_unexpired=False)
    record_account = str((record.get("account") or {}).get("email") or "")
    bindings = {
        "account": account is None or record_account.lower() == account.lower(),
        "thread_id": thread_id is None or str(record.get("thread_id")) == thread_id,
        "draft_id": draft_id is None or str(record.get("draft_id")) == draft_id,
    }
    checked = any(value is not None for value in (account, thread_id, draft_id))
    if checked and not all(bindings.values()):
        mismatched = ", ".join(key for key, matches in bindings.items() if not matches)
        raise AttestationError("ATTESTATION_BINDING_MISMATCH", f"Attestation differs from requested {mismatched}")

    expired = _parse_iso(str(record["expires_at"])) <= _now()
    source = record.get("source") or {}
    return {
        "attestation_id": record["attestation_id"],
        "signature_valid": True,
        "created_at": record["created_at"],
        "expires_at": record["expires_at"],
        "expired": expired,
        "usable": bool(record.get("send_eligible")) and not expired,
        "send_eligible": bool(record.get("send_eligible")),
        "confidence": record.get("confidence"),
        "account_email": record_account,
        "thread_id": record.get("thread_id"),
        "draft_id": record.get("draft_id"),
        "binding_match": all(bindings.values()) if checked else None,
        "delay_seconds": record.get("delay_seconds"),
        "fingerprint": (record.get("fingerprint") or {}).get("exact"),
        "renderer": dict(record.get("renderer") or {}),
        "screenshots": [
            {"path": item.get("path"), "sha256": item.get("sha256")}
            for item in (record.get("screenshots") or [])
        ],
        "summary": {
            "to_count": len(source.get("to") or []),
            "cc_count": len(source.get("cc") or []),
            "bcc_count": len(source.get("bcc") or []),
            "attachment_count": len(source.get("attachments") or []),
            "empty_subject": not bool(str(source.get("subject") or "").strip()),
            "scheduled": bool(source.get("scheduled_for")),
            "has_quote": bool(source.get("quoted_content")),
            "editor_normalized_changed": bool((record.get("normalization") or {}).get("changed")),
        },
    }


def _screenshot_records(result: dict[str, Any]) -> list[dict[str, str]]:
    records = []
    for raw_path in result.get("screenshots") or []:
        path = Path(str(raw_path))
        if not path.exists():
            raise AttestationError("RENDERER_FAILED", f"Renderer screenshot is missing: {path}")
        private_file(path)
        records.append({"path": str(path), "sha256": sha256(path.read_bytes())})
    return records


def _validate_probe(
    result: dict[str, Any],
    *,
    expected_account: dict[str, str],
    thread_id: str,
    draft_id: str,
    sid: str,
    expected_source: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if result.get("dirty") is not False:
        raise AttestationError("DIRTY_RENDERER_DRAFT", "Live Superhuman draft/editor has unsaved state")
    if str(result.get("thread_id") or "") != thread_id or str(result.get("draft_id") or "") != draft_id:
        raise AttestationError("RENDERER_DRAFT_MISMATCH", "Live Superhuman renderer is not on the requested draft/thread")
    if str(result.get("account_email") or "").lower() != expected_account["email"].lower():
        raise AttestationError("RENDERER_ACCOUNT_MISMATCH", "Live Superhuman renderer is bound to a different account")

    live_json = result.get("live_draft_json")
    if not isinstance(live_json, dict):
        raise AttestationError("RENDERER_FAILED", "Renderer did not return the live draft model JSON")
    live_source = source_fields(live_json, attachment_digests={
        str(item.get("uuid") or ""): str(item.get("digest"))
        for item in expected_source.get("attachments") or []
        if item.get("digest")
    })
    if canonical_bytes(live_source) != canonical_bytes(expected_source):
        raise AttestationError("RENDERER_SOURCE_MISMATCH", "Live Superhuman model differs from the selected server draft")

    payload = result.get("outgoing_payload")
    if not isinstance(payload, dict):
        raise AttestationError("RENDERER_FAILED", "Renderer did not return OutgoingMessage.toJsonRequest()")
    if str(payload.get("superhuman_id") or "") != sid:
        raise AttestationError("RENDERER_IDENTITY_MISMATCH", "Renderer did not reuse the reserved Superhuman identity")
    if str(payload.get("thread_id") or "") != thread_id or str(payload.get("message_id") or "") != draft_id:
        raise AttestationError("RENDERER_PAYLOAD_MISMATCH", "Outgoing payload is not bound to the requested draft/thread")
    if _network_writes(list(result.get("network_events") or [])):
        raise AttestationError("RENDERER_WROTE_LIVE_STATE", "Renderer probe observed a prohibited write request")

    app_version = str(result.get("app_version") or "")
    web_version = str(result.get("web_version") or "")
    if not _renderer_build_allowed(app_version, web_version):
        raise AttestationError(
            "RENDERER_VERSION_UNSUPPORTED",
            f"Superhuman renderer build is not allowlisted: {app_version or 'unknown'}@{web_version or 'unknown'}",
        )
    return live_source, payload


def _fingerprint(
    *,
    account: dict[str, str],
    source: dict[str, Any],
    editor_html: str,
    payload: dict[str, Any],
    signature_settings: Any,
    app_version: str,
    web_version: str,
    history_id: Any,
    delay: int,
) -> dict[str, Any]:
    fields = {
        "account": sha256(canonical_bytes(account)),
        "source": sha256(canonical_bytes(source)),
        "raw_body": sha256(source["body"]),
        "editor_html": sha256(editor_html),
        "outgoing_payload": sha256(canonical_bytes(payload)),
        "signature_settings": sha256(canonical_bytes(signature_settings)),
        "renderer_versions": sha256(canonical_bytes({"app": app_version, "web": web_version, "adapter": ADAPTER_VERSION})),
        "history_id": sha256(canonical_bytes(history_id)),
        "transport": sha256(canonical_bytes({"version": 3, "delay": delay, "is_multi_recipient": True})),
    }
    return {"fields": fields, "exact": sha256(canonical_bytes(fields))}


def create(
    thread_id: str,
    draft_id: str,
    *,
    account: str,
    output_dir: Path,
    delay: int = 20,
    renderer: Renderer | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Create a signed attestation without calling any mail write/send endpoint."""
    from . import send  # avoid import cycle

    renderer = renderer or CdpRenderer()
    sid = send._superhuman_id()
    checked_a = send._preflight(
        thread_id,
        draft_id,
        account=account,
        sid=sid,
        require_explicit_account=True,
        allow_empty_subject=True,
    )
    draft_a = checked_a["draft"]
    digests_a = attachment_digests(draft_a)
    source_a = source_fields(draft_a, attachment_digests=digests_a)
    history_a = checked_a["lifecycle"]["observations"][0].get("history_id")

    request = {
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "account_email": checked_a["lifecycle"]["account"]["email"],
        "provider_user_id": checked_a["lifecycle"]["account"]["provider_user_id"],
        "thread_id": thread_id,
        "draft_id": draft_id,
        "superhuman_id": sid,
        "expected_source_sha256": sha256(canonical_bytes(source_a)),
    }
    probe = renderer.probe(request, output_dir=output_dir)
    live_source, payload = _validate_probe(
        probe,
        expected_account=checked_a["lifecycle"]["account"],
        thread_id=thread_id,
        draft_id=draft_id,
        sid=sid,
        expected_source=source_a,
    )

    checked_b = send._preflight(
        thread_id,
        draft_id,
        account=account,
        sid=sid,
        require_explicit_account=True,
        allow_empty_subject=True,
    )
    digests_b = attachment_digests(checked_b["draft"])
    source_b = source_fields(checked_b["draft"], attachment_digests=digests_b)
    history_b = checked_b["lifecycle"]["observations"][0].get("history_id")
    if canonical_bytes(source_a) != canonical_bytes(source_b) or history_a != history_b:
        raise AttestationError("DRAFT_CHANGED_DURING_RENDER", "Server draft/history changed during the renderer probe")

    editor_html = str(probe.get("editor_html") or "")
    if not editor_html:
        raise AttestationError("RENDERER_FAILED", "Renderer did not return editor-normalized HTML")
    app_version = str(probe.get("app_version") or "")
    web_version = str(probe.get("web_version") or "")
    signature_settings = probe.get("signature_settings") or {}
    fingerprint = _fingerprint(
        account=checked_a["lifecycle"]["account"],
        source=live_source,
        editor_html=editor_html,
        payload=payload,
        signature_settings=signature_settings,
        app_version=app_version,
        web_version=web_version,
        history_id=history_a,
        delay=delay,
    )
    created_at = _now()
    record = _seal({
        "schema_version": SCHEMA_VERSION,
        "created_at": _iso(created_at),
        "expires_at": _iso(created_at + timedelta(seconds=ttl_seconds)),
        "send_eligible": True,
        "confidence": "exact_superhuman_renderer",
        "account": checked_a["lifecycle"]["account"],
        "thread_id": thread_id,
        "draft_id": draft_id,
        "superhuman_id": sid,
        "delay_seconds": delay,
        "source": live_source,
        "editor_html": editor_html,
        "normalization": {
            "changed": source_a["body"].encode() != editor_html.encode(),
            "raw_body_sha256": sha256(source_a["body"]),
            "editor_html_sha256": sha256(editor_html),
        },
        "outgoing_payload": payload,
        "signature_settings": signature_settings,
        "renderer": {
            "adapter_version": ADAPTER_VERSION,
            "app_version": app_version,
            "web_version": web_version,
            "surface": probe.get("surface") or "superhuman-desktop",
        },
        "history_id": history_a,
        "fingerprint": fingerprint,
        "screenshots": _screenshot_records(probe),
        "observation": {"attested_at": _iso(created_at)},
    })
    path = save(record, output_dir=output_dir)
    return {**record, "artifact_path": str(path)}


def revalidate_for_send(
    record: dict[str, Any],
    *,
    account: str,
    renderer: Renderer | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Second no-write probe; return only freshly verified payload bytes."""
    from . import send  # avoid import cycle

    verify(record)
    renderer = renderer or CdpRenderer()
    output_dir = output_dir or (_artifact_dir() / str(record["attestation_id"]) / "send-time")
    if record["account"]["email"].lower() != account.lower():
        raise AttestationError("ACCOUNT_BINDING_MISMATCH", "Send account differs from the approved attestation")

    checked_a = send._preflight(
        str(record["thread_id"]),
        str(record["draft_id"]),
        account=account,
        sid=str(record["superhuman_id"]),
        require_explicit_account=True,
        allow_empty_subject=not bool(str(record["source"].get("subject") or "").strip()),
        attempt_superhuman_id=str(record["superhuman_id"]),
    )
    digests = attachment_digests(checked_a["draft"])
    current_source = source_fields(checked_a["draft"], attachment_digests=digests)
    history_a = checked_a["lifecycle"]["observations"][0].get("history_id")
    if canonical_bytes(current_source) != canonical_bytes(record["source"]):
        raise AttestationError("STALE_ATTESTATION", "Server draft differs from the approved source")
    if history_a != record.get("history_id"):
        raise AttestationError("STALE_ATTESTATION", "Server history changed after approval")

    request = {
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "account_email": record["account"]["email"],
        "provider_user_id": record["account"]["provider_user_id"],
        "thread_id": record["thread_id"],
        "draft_id": record["draft_id"],
        "superhuman_id": record["superhuman_id"],
        "expected_source_sha256": sha256(canonical_bytes(record["source"])),
    }
    probe = renderer.probe(request, output_dir=output_dir)
    live_source, payload = _validate_probe(
        probe,
        expected_account=record["account"],
        thread_id=str(record["thread_id"]),
        draft_id=str(record["draft_id"]),
        sid=str(record["superhuman_id"]),
        expected_source=record["source"],
    )

    checked_b = send._preflight(
        str(record["thread_id"]),
        str(record["draft_id"]),
        account=account,
        sid=str(record["superhuman_id"]),
        require_explicit_account=True,
        allow_empty_subject=not bool(str(record["source"].get("subject") or "").strip()),
        attempt_superhuman_id=str(record["superhuman_id"]),
    )
    source_b = source_fields(checked_b["draft"], attachment_digests=attachment_digests(checked_b["draft"]))
    history_b = checked_b["lifecycle"]["observations"][0].get("history_id")
    if canonical_bytes(current_source) != canonical_bytes(source_b) or history_a != history_b:
        raise AttestationError("STALE_ATTESTATION", "Server draft/history changed during send-time rendering")

    fingerprint = _fingerprint(
        account=record["account"],
        source=live_source,
        editor_html=str(probe.get("editor_html") or ""),
        payload=payload,
        signature_settings=probe.get("signature_settings") or {},
        app_version=str(probe.get("app_version") or ""),
        web_version=str(probe.get("web_version") or ""),
        history_id=history_b,
        delay=int(record["delay_seconds"]),
    )
    if not hmac.compare_digest(fingerprint["exact"], str(record["fingerprint"]["exact"])):
        changed = sorted(
            key
            for key, approved in record["fingerprint"]["fields"].items()
            if fingerprint["fields"].get(key) != approved
        )
        raise AttestationError(
            "STALE_ATTESTATION",
            "Exact renderer output changed after approval" + (f" ({', '.join(changed)})" if changed else ""),
        )
    if canonical_bytes(payload) != canonical_bytes(record["outgoing_payload"]):
        raise AttestationError("STALE_ATTESTATION", "Fresh outgoing payload bytes differ from approval")

    return {
        "outgoing_payload": payload,
        "outgoing_payload_bytes": canonical_bytes(payload),
        "fingerprint": fingerprint,
        "observation": {"send_time_probed_at": _iso(_now())},
        "screenshots": _screenshot_records(probe),
        "preflight": checked_b,
    }
