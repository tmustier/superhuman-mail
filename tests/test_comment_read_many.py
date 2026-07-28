"""Tests for batched, fail-closed Superhuman comment reads."""
from __future__ import annotations

import json
from unittest.mock import patch

from superhuman_mail.comment import read, read_many


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def _thread_value(comment_id: str) -> dict:
    return {
        "teams": {
            "team": {
                "containers": {
                    "container": {
                        "messages": {
                            comment_id: {
                                "comment": {
                                    "id": comment_id,
                                    "body": f"<div><p>{comment_id}</p></div>",
                                    "createdAt": "2026-07-28T12:00:00Z",
                                },
                                "sharing": {"name": "Thomas", "by": "thomas@nexcade.ai"},
                                "mentions": [],
                            }
                        }
                    }
                }
            }
        }
    }


def test_read_many_batches_threads_and_preserves_mapping():
    responses = [
        _Response({"results": [{"value": _thread_value("comment-a")}, {"value": _thread_value("comment-b")}]}),
        _Response({"results": [{"value": _thread_value("comment-c")}]}),
    ]
    with (
        patch("superhuman_mail.comment._config.api", return_value="google-id"),
        patch("superhuman_mail.comment._auth.api_headers", return_value={"Authorization": "Bearer test"}),
        patch("superhuman_mail.comment.urllib.request.urlopen", side_effect=responses) as urlopen,
    ):
        result = read_many(["thread-a", "thread-b", "thread-c"], batch_size=2)

    assert result["status"] == "succeeded"
    assert result["data"]["complete"] is True
    assert result["data"]["requested_count"] == 3
    assert result["data"]["failed_count"] == 0
    assert result["data"]["comments_by_thread"]["thread-b"][0]["id"] == "comment-b"
    assert urlopen.call_count == 2


def test_read_many_treats_null_or_omitted_userdata_as_empty():
    with (
        patch("superhuman_mail.comment._config.api", return_value="google-id"),
        patch("superhuman_mail.comment._auth.api_headers", return_value={}),
        patch(
            "superhuman_mail.comment.urllib.request.urlopen",
            return_value=_Response(
                {
                    "results": [
                        {"path": "users/google-id/threads/thread-a", "value": _thread_value("comment-a")},
                        {"path": "users/google-id/threads/thread-b", "value": None},
                    ]
                }
            ),
        ),
    ):
        result = read_many(["thread-a", "thread-b", "thread-c"], batch_size=2)

    assert result["status"] == "succeeded"
    assert result["data"]["complete"] is True
    assert result["data"]["comments_by_thread"]["thread-b"] == []
    assert result["data"]["comments_by_thread"]["thread-c"] == []


def test_read_many_fails_closed_when_a_batch_request_fails():
    with (
        patch("superhuman_mail.comment._config.api", return_value="google-id"),
        patch("superhuman_mail.comment._auth.api_headers", return_value={}),
        patch("superhuman_mail.comment.urllib.request.urlopen", side_effect=TimeoutError("slow API")),
    ):
        result = read_many(["thread-a", "thread-b"])

    assert result["status"] == "failed"
    assert result["data"]["complete"] is False
    assert result["data"]["succeeded_count"] == 0
    assert result["data"]["failed_count"] == 2
    assert result["data"]["failures"][0]["thread_id"] == "thread-a"
    assert result["errors"][0]["code"] == "PARTIAL_COMMENT_READ"


def test_single_read_retains_existing_envelope_shape():
    with (
        patch("superhuman_mail.comment._config.api", return_value="google-id"),
        patch("superhuman_mail.comment._auth.api_headers", return_value={}),
        patch(
            "superhuman_mail.comment.urllib.request.urlopen",
            return_value=_Response({"results": [{"value": _thread_value("comment-a")}]}),
        ),
    ):
        result = read("thread-a")

    assert result["status"] == "succeeded"
    assert result["command"] == "comment.read"
    assert result["data"]["thread_id"] == "thread-a"
    assert result["data"]["comment_count"] == 1
