"""Tests for merging message-level attachments into drafts at send time."""
from superhuman_mail.send import _attachments_json, _build_outgoing, _merge_message_attachments


def _att(uuid: str, name: str = "file.pdf", discarded=None) -> dict:
    return {
        "uuid": uuid,
        "name": name,
        "type": "application/pdf",
        "inline": False,
        "source": {"type": "upload-firebase", "threadId": "t1", "messageId": "d1", "uuid": uuid},
        "discardedAt": discarded,
    }


def test_merges_message_level_attachments_into_draft():
    draft = {"id": "d1", "threadId": "t1"}
    msg_data = {"attachments": {"u1": _att("u1")}}
    merged = _merge_message_attachments(draft, msg_data)
    assert [a["uuid"] for a in merged["attachments"]] == ["u1"]
    # original draft untouched
    assert "attachments" not in draft


def test_skips_discarded_and_duplicate_attachments():
    draft = {"id": "d1", "threadId": "t1", "attachments": [_att("u1")]}
    msg_data = {"attachments": {"u1": _att("u1"), "u2": _att("u2", discarded="2026-01-01T00:00:00Z"), "u3": _att("u3")}}
    merged = _merge_message_attachments(draft, msg_data)
    assert sorted(a["uuid"] for a in merged["attachments"]) == ["u1", "u3"]


def test_no_attachments_leaves_draft_unchanged():
    draft = {"id": "d1", "threadId": "t1"}
    merged = _merge_message_attachments(draft, {})
    assert merged is draft


def test_outgoing_payload_includes_merged_attachments():
    draft = {"id": "d1", "threadId": "t1", "subject": "s", "body": "b"}
    msg_data = {"attachments": {"u1": _att("u1", name="report.pdf")}}
    merged = _merge_message_attachments(draft, msg_data)
    outgoing = _build_outgoing(merged, sid="sid.test")
    assert outgoing["attachments"] == _attachments_json(merged["attachments"])
    assert outgoing["attachments"][0]["name"] == "report.pdf"
    assert outgoing["attachments"][0]["source"]["type"] == "upload-firebase"
