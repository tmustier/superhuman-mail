"""Tests for read-only received-attachment downloads."""

from __future__ import annotations

import hashlib
import json
import stat
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from superhuman_mail import Client, attachment
from superhuman_mail._auth import MediaSessionCredential


class FakeResponse:
    def __init__(self, data: bytes, content_type: str = "application/pdf") -> None:
        self._data = data
        self._read = False
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def read(self, _size: int) -> bytes:
        if self._read:
            return b""
        self._read = True
        return self._data


class InterruptedResponse(FakeResponse):
    def read(self, size: int) -> bytes:
        if self._read:
            raise TimeoutError("private network details")
        return super().read(size)


def _raw_thread(*attachments: dict[str, object]) -> dict[str, object]:
    return {
        "messages": [
            {
                "id": "message-1",
                "attachments": list(attachments),
            }
        ]
    }


def _attachment(
    attachment_id: str,
    name: str,
    data: bytes,
    **extra: object,
) -> tuple[dict[str, object], bytes]:
    return (
        {
            "attachmentId": attachment_id,
            "messageId": "message-1",
            "name": name,
            "type": "application/pdf",
            "size": len(data),
            **extra,
        },
        data,
    )


def _credential(provider_id: str = "1111111111") -> MediaSessionCredential:
    return MediaSessionCredential(provider_id, "cookie-secret")


def test_downloads_all_attachments_atomically_with_private_files(
    tmp_path: Path,
) -> None:
    first, first_bytes = _attachment("attachment-1", "report.pdf", b"%PDF-first")
    second, second_bytes = _attachment("attachment-2", "report.pdf", b"%PDF-second")
    private_parent = tmp_path / "new-private-parent"
    output = private_parent / "new-private-directory"

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(first, second),
    ) as get_thread:
        with patch(
            "superhuman_mail.attachment._auth.media_session_credentials",
            return_value=[_credential()],
        ):
            with patch(
                "superhuman_mail.attachment._open_media",
                side_effect=[FakeResponse(first_bytes), FakeResponse(second_bytes)],
            ):
                result = attachment.download(
                    "thread-1",
                    output,
                    account="second@example.com",
                )

    assert result["status"] == "succeeded"
    assert result["data"]["account_email"] == "second@example.com"
    assert result["data"]["attachment_count"] == 2
    assert result["data"]["total_bytes"] == len(first_bytes) + len(second_bytes)
    assert (output / "report.pdf").read_bytes() == first_bytes
    assert (output / "report (2).pdf").read_bytes() == second_bytes
    assert stat.S_IMODE(private_parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE((output / "report.pdf").stat().st_mode) == 0o600
    assert result["data"]["attachments"][0]["sha256"] == (
        "sha256:" + hashlib.sha256(first_bytes).hexdigest()
    )
    assert "cookie-secret" not in json.dumps(result)
    get_thread.assert_called_once_with("thread-1", "second@example.com")


def test_tries_each_local_media_identity_without_exposing_credentials(
    tmp_path: Path,
) -> None:
    raw_attachment, data = _attachment("attachment-1", "report.pdf", b"%PDF-data")
    wrong_identity = urllib.error.HTTPError(
        "https://media.superhuman.com/redacted",
        520,
        "wrong account edge response",
        Message(),
        None,
    )
    credentials = [
        _credential("1111111111"),
        _credential("2222222222"),
        _credential("3333333333"),
    ]

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(raw_attachment),
    ):
        with patch(
            "superhuman_mail.attachment._auth.media_session_credentials",
            return_value=credentials,
        ):
            with patch(
                "superhuman_mail.attachment._open_media",
                side_effect=[
                    wrong_identity,
                    FakeResponse(b"login", content_type="text/html"),
                    FakeResponse(data),
                ],
            ) as open_media:
                result = attachment.download("thread-1", tmp_path / "out")

    assert result["status"] == "succeeded"
    assert open_media.call_count == 3
    assert open_media.call_args_list[0].args[1] is credentials[0]
    assert open_media.call_args_list[1].args[1] is credentials[1]
    assert open_media.call_args_list[2].args[1] is credentials[2]
    assert "cookie-secret" not in json.dumps(result)


def test_all_404_responses_report_attachment_not_found(tmp_path: Path) -> None:
    raw_attachment, _data = _attachment("attachment-1", "report.pdf", b"missing")
    missing = urllib.error.HTTPError(
        "https://media.superhuman.com/redacted",
        404,
        "missing",
        Message(),
        None,
    )

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(raw_attachment),
    ):
        with patch(
            "superhuman_mail.attachment._auth.media_session_credentials",
            return_value=[_credential()],
        ):
            with patch(
                "superhuman_mail.attachment._open_media",
                side_effect=missing,
            ):
                result = attachment.download("thread-1", tmp_path / "out")

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "ATTACHMENT_NOT_FOUND"


def test_all_unauthorized_responses_report_expired_session(tmp_path: Path) -> None:
    raw_attachment, _data = _attachment("attachment-1", "report.pdf", b"missing")
    unauthorized = urllib.error.HTTPError(
        "https://media.superhuman.com/redacted",
        401,
        "unauthorized",
        Message(),
        None,
    )

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(raw_attachment),
    ):
        with patch(
            "superhuman_mail.attachment._auth.media_session_credentials",
            return_value=[_credential()],
        ):
            with patch(
                "superhuman_mail.attachment._open_media",
                side_effect=unauthorized,
            ):
                result = attachment.download("thread-1", tmp_path / "out")

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "MEDIA_SESSION_EXPIRED"
    assert "cookie-secret" not in json.dumps(result)


def test_size_mismatch_removes_staged_bytes(tmp_path: Path) -> None:
    raw_attachment, _data = _attachment("attachment-1", "report.pdf", b"expected")
    output = tmp_path / "out"

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(raw_attachment),
    ):
        with patch(
            "superhuman_mail.attachment._auth.media_session_credentials",
            return_value=[_credential()],
        ):
            with patch(
                "superhuman_mail.attachment._open_media",
                return_value=FakeResponse(b"short"),
            ):
                result = attachment.download("thread-1", output)

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "ATTACHMENT_SIZE_MISMATCH"
    assert list(output.iterdir()) == []


def test_interrupted_stream_removes_partial_bytes(tmp_path: Path) -> None:
    raw_attachment, _data = _attachment("attachment-1", "report.pdf", b"expected")
    output = tmp_path / "out"

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(raw_attachment),
    ):
        with patch(
            "superhuman_mail.attachment._auth.media_session_credentials",
            return_value=[_credential()],
        ):
            with patch(
                "superhuman_mail.attachment._open_media",
                return_value=InterruptedResponse(b"partial"),
            ):
                result = attachment.download("thread-1", output)

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "ATTACHMENT_DOWNLOAD_INTERRUPTED"
    assert result["errors"][0]["retryable"] is True
    assert "private network details" not in json.dumps(result)
    assert list(output.iterdir()) == []


def test_html_error_document_is_not_written_as_attachment(tmp_path: Path) -> None:
    html = b"<html>login page</html>"
    raw_attachment, _data = _attachment("attachment-1", "report.pdf", html)
    output = tmp_path / "out"

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(raw_attachment),
    ):
        with patch(
            "superhuman_mail.attachment._auth.media_session_credentials",
            return_value=[_credential()],
        ):
            with patch(
                "superhuman_mail.attachment._open_media",
                return_value=FakeResponse(html, content_type="text/html"),
            ):
                result = attachment.download("thread-1", output)

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "INVALID_ATTACHMENT_RESPONSE"
    assert list(output.iterdir()) == []


def test_existing_output_is_never_overwritten_or_fetched(tmp_path: Path) -> None:
    raw_attachment, _data = _attachment("attachment-1", "report.pdf", b"expected")
    output = tmp_path / "out"
    output.mkdir()
    existing = output / "report.pdf"
    existing.write_bytes(b"keep-me")

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(raw_attachment),
    ):
        with patch("superhuman_mail.attachment._open_media") as open_media:
            result = attachment.download("thread-1", output)

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "OUTPUT_CONFLICT"
    assert existing.read_bytes() == b"keep-me"
    open_media.assert_not_called()


def test_selector_downloads_only_the_exact_attachment(tmp_path: Path) -> None:
    first, _first_bytes = _attachment("attachment-1", "first.pdf", b"%PDF-first")
    second, second_bytes = _attachment("attachment-2", "second.pdf", b"%PDF-second")

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(first, second),
    ):
        with patch(
            "superhuman_mail.attachment._auth.media_session_credentials",
            return_value=[_credential()],
        ):
            with patch(
                "superhuman_mail.attachment._open_media",
                return_value=FakeResponse(second_bytes),
            ):
                result = attachment.download(
                    "thread-1",
                    tmp_path / "out",
                    attachment_id="attachment-2",
                )

    assert result["status"] == "succeeded"
    assert result["data"]["attachment_count"] == 1
    assert result["data"]["attachments"][0]["attachment_id"] == "attachment-2"
    assert not (tmp_path / "out" / "first.pdf").exists()
    assert (tmp_path / "out" / "second.pdf").read_bytes() == second_bytes


def test_thread_missing_from_local_cache_has_explicit_safe_error(
    tmp_path: Path,
) -> None:
    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        side_effect=RuntimeError("Thread not found in local DB: private-thread-id"),
    ):
        result = attachment.download("private-thread-id", tmp_path / "out")

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "THREAD_NOT_IN_LOCAL_CACHE"
    assert "private-thread-id" not in json.dumps(result)
    assert not (tmp_path / "out").exists()


def test_wrong_attachment_selector_fails_without_network(tmp_path: Path) -> None:
    raw_attachment, _data = _attachment("attachment-1", "report.pdf", b"expected")

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(raw_attachment),
    ):
        with patch("superhuman_mail.attachment._open_media") as open_media:
            result = attachment.download(
                "thread-1",
                tmp_path / "out",
                attachment_id="wrong-attachment-id",
            )

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "ATTACHMENT_NOT_FOUND"
    assert not (tmp_path / "out").exists()
    open_media.assert_not_called()


def test_id_only_metadata_is_downloadable_and_selectable(tmp_path: Path) -> None:
    raw_attachment, data = _attachment("attachment-1", "report.pdf", b"%PDF-data")
    raw_attachment["id"] = raw_attachment.pop("attachmentId")

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(raw_attachment),
    ):
        with patch(
            "superhuman_mail.attachment._auth.media_session_credentials",
            return_value=[_credential()],
        ):
            with patch(
                "superhuman_mail.attachment._open_media",
                return_value=FakeResponse(data),
            ):
                result = attachment.download(
                    "thread-1",
                    tmp_path / "out",
                    attachment_id="attachment-1",
                )

    assert result["status"] == "succeeded"
    assert result["data"]["attachments"][0]["attachment_id"] == "attachment-1"


def test_media_url_encodes_every_untrusted_path_component() -> None:
    spec = attachment.AttachmentSpec(
        message_id="message/1",
        attachment_id="attachment ?",
        filename="report.pdf",
        media_type="application/pdf",
        expected_size=1,
        inline=False,
    )
    credential = MediaSessionCredential("1111111111", "cookie-secret")

    url = attachment._media_url(spec, credential)

    assert url == (
        "https://media.superhuman.com/v2/attachments/"
        "1111111111/message%2F1/attachment%20%3F"
    )
    assert "cookie-secret" not in url


def test_untrusted_filename_cannot_escape_output_directory(tmp_path: Path) -> None:
    raw_attachment, data = _attachment(
        "attachment-1",
        "../../private:report.pdf",
        b"%PDF-data",
    )

    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=_raw_thread(raw_attachment),
    ):
        with patch(
            "superhuman_mail.attachment._auth.media_session_credentials",
            return_value=[_credential()],
        ):
            with patch(
                "superhuman_mail.attachment._open_media",
                return_value=FakeResponse(data),
            ):
                result = attachment.download("thread-1", tmp_path / "out")

    assert result["status"] == "succeeded"
    written = list((tmp_path / "out").iterdir())
    assert len(written) == 1
    assert written[0].parent == tmp_path / "out"
    assert "/" not in written[0].name
    assert ":" not in written[0].name
    assert not (tmp_path / "private:report.pdf").exists()


def test_missing_provider_identifier_fails_without_network(tmp_path: Path) -> None:
    raw = _raw_thread(
        {
            "messageId": "message-1",
            "name": "unavailable.pdf",
            "type": "application/pdf",
            "size": 10,
        }
    )
    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=raw,
    ):
        with patch("superhuman_mail.attachment._open_media") as open_media:
            result = attachment.download("thread-1", tmp_path / "out")

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "ATTACHMENT_SOURCE_UNSUPPORTED"
    open_media.assert_not_called()


def test_declared_limit_fails_before_authentication(tmp_path: Path) -> None:
    raw = _raw_thread(
        {
            "attachmentId": "attachment-1",
            "messageId": "message-1",
            "name": "huge.bin",
            "size": attachment.MAX_ATTACHMENT_BYTES + 1,
        }
    )
    with patch(
        "superhuman_mail.attachment._local.get_thread_json",
        return_value=raw,
    ):
        with patch(
            "superhuman_mail.attachment._auth.media_session_credentials"
        ) as credentials:
            result = attachment.download("thread-1", tmp_path / "out")

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "ATTACHMENT_LIMIT_EXCEEDED"
    credentials.assert_not_called()


def test_redirect_handler_blocks_unknown_hosts_and_strips_cross_host_cookie() -> None:
    handler = attachment._SafeAttachmentRedirectHandler()
    request = urllib.request.Request(
        "https://media.superhuman.com/start",
        headers={"Cookie": "secret=value"},
    )

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        Message(),
        "https://storage.googleapis.com/file",
    )
    assert redirected is not None
    assert redirected.get_header("Cookie") is None

    try:
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            Message(),
            "https://attacker.example/file",
        )
    except urllib.error.HTTPError as exc:
        assert exc.reason == "Unsafe attachment redirect blocked"
    else:
        raise AssertionError("unknown redirect host was not blocked")


def test_python_client_exposes_attachment_download(tmp_path: Path) -> None:
    expected = {"status": "succeeded"}
    client = Client()
    with patch(
        "superhuman_mail.client._attachment.download",
        return_value=expected,
    ) as download:
        result = client.attachment.download(
            "thread-1",
            str(tmp_path),
            account="owner@example.com",
        )
    assert result == expected
    download.assert_called_once_with(
        "thread-1",
        str(tmp_path),
        account="owner@example.com",
    )
