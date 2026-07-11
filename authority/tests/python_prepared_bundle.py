from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from superhuman_mail import attestation

now = datetime.now(timezone.utc)
images = [b"\x89PNG\r\n\x1a\ncompose", b"\x89PNG\r\n\x1a\noutgoing"]
payload = {key: None for key in attestation.OUTGOING_FIELDS}
payload.update({
    "headers": [], "superhuman_id": "send-1", "thread_id": "thread-1", "message_id": "draft-1",
    "from": {"email": "owner@example.test"}, "to": [{"email": "recipient@example.test"}], "cc": [], "bcc": [],
    "subject": "Fixture", "html_body": "<p>Fixture</p>", "attachments": [], "abort_on_reply": False,
    "current_message_ids": [], "mail_merge_recipients": [],
})
record = {
    "schema_version": 1, "created_at": attestation._iso(now), "expires_at": attestation._iso(now + timedelta(minutes=15)),
    "send_eligible": True, "confidence": "exact_superhuman_renderer",
    "account": {"provider_user_id": "provider-1", "email": "owner@example.test"},
    "thread_id": "thread-1", "draft_id": "draft-1", "superhuman_id": "send-1", "delay_seconds": 20,
    "source": {}, "editor_html": "<p>Fixture</p>", "normalization": {}, "outgoing_payload": payload,
    "signature_settings": {"opacity": 0.5, "astral_😀": "preserved"},
    "renderer": {"adapter_version": "fixture", "app_version": "fixture", "web_version": "fixture"},
    "history_id": 1, "fingerprint": {"exact": attestation.sha256(b"fingerprint"), "fields": {}},
    "screenshots": [
        {"role": role, "path": f"/executor-state/{role}.png", "sha256": attestation.sha256(image)}
        for role, image in zip(("compose", "outgoing"), images, strict=True)
    ],
    "observation": {}, "signature": "executor-prepared:v1",
}
identity = attestation.canonical_bytes(attestation.identity_content(record))
record["attestation_id"] = attestation.sha256(identity)
print(json.dumps({
    "record": record,
    "identity_base64": base64.b64encode(identity).decode(),
    "screenshots": [
        {"role": role, "sha256": attestation.sha256(image), "media_type": "image/png", "data_base64": base64.b64encode(image).decode()}
        for role, image in zip(("compose", "outgoing"), images, strict=True)
    ],
}))
