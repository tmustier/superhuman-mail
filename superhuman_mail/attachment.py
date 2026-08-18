"""Read-only received-attachment downloads through Superhuman's media service."""
from __future__ import annotations

import hashlib
import http.client
import mimetypes
import os
import re
import tempfile
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from . import _auth, _config, _local
from ._envelope import classify_exception, error, fail, ok

MAX_ATTACHMENT_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_ALLOWED_REDIRECT_HOST_SUFFIXES = (
    ".googleapis.com",
    ".googleusercontent.com",
    ".superhuman.com",
    ".firebaseio.com",
)


class AttachmentDownloadError(RuntimeError):
    """A privacy-safe, typed attachment download failure."""

    def __init__(
        self,
        code: str,
        hint: str,
        *,
        error_class: str = "input",
        retryable: bool = False,
    ) -> None:
        super().__init__(hint)
        self.code = code
        self.hint = hint
        self.error_class = error_class
        self.retryable = retryable


@dataclass(frozen=True)
class AttachmentSpec:
    message_id: str
    attachment_id: str
    filename: str
    media_type: str
    expected_size: int | None
    inline: bool


@dataclass(frozen=True)
class StagedAttachment:
    spec: AttachmentSpec
    temporary_path: Path
    final_path: Path
    size_bytes: int
    sha256: str
    response_media_type: str


class _SafeAttachmentRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep media cookies on their exact host and reject unknown redirects."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        target = urlsplit(newurl)
        hostname = (target.hostname or "").lower()
        allowed = target.scheme == "https" and any(
            hostname == suffix[1:] or hostname.endswith(suffix)
            for suffix in _ALLOWED_REDIRECT_HOST_SUFFIXES
        )
        if not allowed:
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "Unsafe attachment redirect blocked",
                headers,
                fp,
            )

        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        source_hostname = (urlsplit(req.full_url).hostname or "").lower()
        if hostname != source_hostname:
            redirected.remove_header("Cookie")
        return redirected


def _safe_filename(value: Any, index: int, media_type: str) -> str:
    raw = unicodedata.normalize("NFC", str(value or ""))
    cleaned = re.sub(r"[\x00-\x1f\x7f/\\:]", "_", raw).strip().lstrip(".")
    if not cleaned:
        extension = mimetypes.guess_extension(media_type, strict=False) or ""
        cleaned = f"attachment-{index}{extension}"

    encoded = cleaned.encode("utf-8")
    if len(encoded) <= 240:
        return cleaned

    suffix = Path(cleaned).suffix
    if len(suffix.encode("utf-8")) > 32:
        suffix = ""
    stem = cleaned[: -len(suffix)] if suffix else cleaned
    digest = hashlib.sha256(encoded).hexdigest()[:8]
    reserved = len(f"-{digest}{suffix}".encode("utf-8"))
    while stem and len(stem.encode("utf-8")) + reserved > 240:
        stem = stem[:-1]
    return f"{stem or 'attachment'}-{digest}{suffix}"


def _expected_size(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AttachmentDownloadError(
            "INVALID_ATTACHMENT_METADATA",
            "Cached attachment size is invalid; reopen Superhuman to refresh the thread",
            error_class="conflict",
        )
    return value


def _attachment_specs(
    raw_thread: dict[str, Any],
    *,
    message_id: str | None,
    attachment_id: str | None,
) -> list[AttachmentSpec]:
    specs: list[AttachmentSpec] = []
    seen: set[tuple[str, str]] = set()
    index = 0

    for raw_message in raw_thread.get("messages", []) or []:
        if not isinstance(raw_message, dict):
            continue
        cached_message_id = str(raw_message.get("id") or "")
        if message_id is not None and cached_message_id != message_id:
            continue

        for raw_attachment in raw_message.get("attachments", []) or []:
            if not isinstance(raw_attachment, dict) or raw_attachment.get("discardedAt"):
                continue
            source = raw_attachment.get("source") or {}
            if not isinstance(source, dict):
                source = {}
            candidate_id = str(
                raw_attachment.get("attachmentId")
                or raw_attachment.get("id")
                or source.get("attachmentId")
                or ""
            )
            candidate_message_id = str(
                raw_attachment.get("messageId")
                or source.get("messageId")
                or cached_message_id
            )
            if attachment_id is not None and candidate_id != attachment_id:
                continue
            if not candidate_id or not candidate_message_id:
                raise AttachmentDownloadError(
                    "ATTACHMENT_SOURCE_UNSUPPORTED",
                    "A selected attachment has no provider byte identifier",
                    error_class="conflict",
                )

            identity = (candidate_message_id, candidate_id)
            if identity in seen:
                continue
            seen.add(identity)
            index += 1
            media_type = str(
                raw_attachment.get("type")
                or raw_attachment.get("contentType")
                or "application/octet-stream"
            )
            specs.append(
                AttachmentSpec(
                    message_id=candidate_message_id,
                    attachment_id=candidate_id,
                    filename=_safe_filename(
                        raw_attachment.get("name") or raw_attachment.get("filename"),
                        index,
                        media_type,
                    ),
                    media_type=media_type,
                    expected_size=_expected_size(raw_attachment.get("size")),
                    inline=bool(raw_attachment.get("inline")),
                )
            )

    if not specs:
        code = "ATTACHMENT_NOT_FOUND" if message_id or attachment_id else "NO_ATTACHMENTS"
        raise AttachmentDownloadError(
            code,
            "No attachment matched the requested thread and selectors",
            error_class="not-found",
        )
    return specs


def _unique_final_paths(specs: list[AttachmentSpec], output_dir: Path) -> list[Path]:
    paths: list[Path] = []
    used: set[str] = set()
    for spec in specs:
        original = Path(spec.filename)
        candidate = original.name
        counter = 2
        while candidate.casefold() in used:
            candidate = f"{original.stem} ({counter}){original.suffix}"
            counter += 1
        used.add(candidate.casefold())
        path = output_dir / candidate
        if path.exists() or path.is_symlink():
            raise AttachmentDownloadError(
                "OUTPUT_CONFLICT",
                f"Refusing to overwrite existing output file: {path}",
                error_class="conflict",
            )
        paths.append(path)
    return paths


def _prepare_output_directory(output: str | Path) -> Path:
    requested = Path(output).expanduser()
    missing: list[Path] = []
    cursor = requested
    try:
        while not cursor.exists():
            if cursor.is_symlink():
                raise AttachmentDownloadError(
                    "OUTPUT_DIRECTORY_INVALID",
                    f"Attachment output contains a broken symbolic link: {cursor}",
                )
            missing.append(cursor)
            if cursor == cursor.parent:
                break
            cursor = cursor.parent

        if cursor.exists() and not cursor.is_dir():
            raise AttachmentDownloadError(
                "OUTPUT_DIRECTORY_INVALID",
                f"Attachment output parent is not a directory: {cursor}",
            )

        for directory in reversed(missing):
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                if directory.is_symlink() or not directory.is_dir():
                    raise AttachmentDownloadError(
                        "OUTPUT_DIRECTORY_INVALID",
                        "Attachment output changed while private directories were being created",
                    )
            else:
                os.chmod(directory, 0o700, follow_symlinks=False)

        if not requested.is_dir():
            raise AttachmentDownloadError(
                "OUTPUT_DIRECTORY_INVALID",
                f"Attachment output is not a directory: {requested}",
            )
        return requested.resolve(strict=True)
    except AttachmentDownloadError:
        raise
    except OSError as exc:
        raise AttachmentDownloadError(
            "OUTPUT_DIRECTORY_INVALID",
            f"Cannot prepare attachment output directory: {exc}",
        ) from exc


def _media_url(spec: AttachmentSpec, credential: _auth.MediaSessionCredential) -> str:
    parts = (
        credential.provider_id,
        spec.message_id,
        spec.attachment_id,
    )
    encoded = "/".join(quote(part, safe="") for part in parts)
    return f"https://media.superhuman.com/v2/attachments/{encoded}"


def _open_media(
    spec: AttachmentSpec,
    credential: _auth.MediaSessionCredential,
) -> Any:
    opener = urllib.request.build_opener(_SafeAttachmentRedirectHandler())
    request = urllib.request.Request(
        _media_url(spec, credential),
        headers={"Cookie": f"{credential.provider_id}={credential.cookie_value}"},
        method="GET",
    )
    return opener.open(request, timeout=60)


def _stage_response(
    response: Any,
    spec: AttachmentSpec,
    final_path: Path,
    *,
    byte_limit: int,
) -> StagedAttachment:
    response_media_type = str(response.headers.get_content_type()).lower()
    expected_media_type = spec.media_type.partition(";")[0].strip().lower()
    if (
        response_media_type in {"application/json", "text/html"}
        and response_media_type != expected_media_type
    ):
        raise AttachmentDownloadError(
            "INVALID_ATTACHMENT_RESPONSE",
            "Superhuman's media service returned an error document instead of attachment bytes",
            error_class="network",
            retryable=True,
        )

    fd, temporary_name = tempfile.mkstemp(
        prefix=".shm-attachment-",
        dir=final_path.parent,
    )
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    total = 0
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(fd, "wb") as handle:
            while chunk := response.read(_CHUNK_BYTES):
                total += len(chunk)
                if total > byte_limit:
                    raise AttachmentDownloadError(
                        "ATTACHMENT_LIMIT_EXCEEDED",
                        "Attachment bytes exceeded the command's documented safety limit",
                        error_class="conflict",
                    )
                handle.write(chunk)
                digest.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        if spec.expected_size is not None and total != spec.expected_size:
            raise AttachmentDownloadError(
                "ATTACHMENT_SIZE_MISMATCH",
                "Downloaded bytes did not match Superhuman's attachment metadata",
                error_class="conflict",
                retryable=True,
            )
        return StagedAttachment(
            spec=spec,
            temporary_path=temporary_path,
            final_path=final_path,
            size_bytes=total,
            sha256=f"sha256:{digest.hexdigest()}",
            response_media_type=response_media_type,
        )
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _stage_one(
    spec: AttachmentSpec,
    final_path: Path,
    credential: _auth.MediaSessionCredential,
    *,
    byte_limit: int,
) -> StagedAttachment:
    with _open_media(spec, credential) as response:
        return _stage_response(
            response,
            spec,
            final_path,
            byte_limit=byte_limit,
        )


def _safe_http_error(exc: urllib.error.HTTPError) -> AttachmentDownloadError:
    if exc.code in (401, 403):
        return AttachmentDownloadError(
            "MEDIA_SESSION_EXPIRED",
            "Superhuman's media session is unavailable; open the desktop app and retry",
            error_class="auth",
            retryable=True,
        )
    if exc.code == 404:
        return AttachmentDownloadError(
            "ATTACHMENT_NOT_FOUND",
            "The attachment is no longer available from Superhuman's media service",
            error_class="not-found",
        )
    return AttachmentDownloadError(
        f"HTTP_{exc.code}",
        f"Superhuman's media service returned HTTP {exc.code}",
        error_class="network",
        retryable=exc.code >= 500,
    )


def _stage_first(
    spec: AttachmentSpec,
    final_path: Path,
    credentials: list[_auth.MediaSessionCredential],
    *,
    byte_limit: int,
) -> tuple[_auth.MediaSessionCredential, StagedAttachment]:
    response_statuses: list[int] = []
    invalid_responses: list[AttachmentDownloadError] = []
    for credential in credentials:
        try:
            staged = _stage_one(
                spec,
                final_path,
                credential,
                byte_limit=byte_limit,
            )
            return credential, staged
        except urllib.error.HTTPError as exc:
            response_statuses.append(exc.code)
            if exc.fp is not None:
                exc.close()
        except AttachmentDownloadError as exc:
            if exc.code not in {
                "ATTACHMENT_SIZE_MISMATCH",
                "INVALID_ATTACHMENT_RESPONSE",
            }:
                raise
            invalid_responses.append(exc)

    server_status = next(
        (status for status in reversed(response_statuses) if status >= 500),
        None,
    )
    if server_status is not None:
        raise AttachmentDownloadError(
            f"HTTP_{server_status}",
            f"Superhuman's media service returned HTTP {server_status}",
            error_class="network",
            retryable=True,
        )
    if invalid_responses:
        raise invalid_responses[-1]
    if response_statuses and all(status == 404 for status in response_statuses):
        raise AttachmentDownloadError(
            "ATTACHMENT_NOT_FOUND",
            "The attachment is no longer available from Superhuman's media service",
            error_class="not-found",
        )
    raise AttachmentDownloadError(
        "MEDIA_SESSION_EXPIRED",
        "No signed-in Superhuman media session could access the attachment; open the desktop app and retry",
        error_class="auth",
        retryable=True,
    )


def _commit_staged(staged: list[StagedAttachment]) -> None:
    created: list[Path] = []
    try:
        for item in staged:
            os.link(item.temporary_path, item.final_path, follow_symlinks=False)
            created.append(item.final_path)
            item.temporary_path.unlink()
    except Exception as exc:
        for path in created:
            path.unlink(missing_ok=True)
        for item in staged:
            item.temporary_path.unlink(missing_ok=True)
        if isinstance(exc, FileExistsError):
            raise AttachmentDownloadError(
                "OUTPUT_CONFLICT",
                "An attachment output file appeared during download; nothing was overwritten",
                error_class="conflict",
            ) from exc
        raise


def download(
    thread_id: str,
    output: str | Path,
    *,
    account: str | None = None,
    message_id: str | None = None,
    attachment_id: str | None = None,
) -> dict[str, Any]:
    """Download selected received attachments without mutating mail state."""
    command = "attachment.download"
    staged: list[StagedAttachment] = []
    try:
        try:
            raw_thread = _local.get_thread_json(thread_id, account)
        except RuntimeError as exc:
            if str(exc).startswith("Thread not found in local DB:"):
                raise AttachmentDownloadError(
                    "THREAD_NOT_IN_LOCAL_CACHE",
                    "The thread metadata is not present in Superhuman's local sync cache",
                    error_class="not-found",
                ) from exc
            raise
        specs = _attachment_specs(
            raw_thread,
            message_id=message_id,
            attachment_id=attachment_id,
        )
        declared_total = sum(spec.expected_size or 0 for spec in specs)
        if any(
            spec.expected_size is not None
            and spec.expected_size > MAX_ATTACHMENT_BYTES
            for spec in specs
        ) or declared_total > MAX_TOTAL_BYTES:
            raise AttachmentDownloadError(
                "ATTACHMENT_LIMIT_EXCEEDED",
                "Selected attachment metadata exceeds the command's documented safety limits",
                error_class="conflict",
            )

        output_dir = _prepare_output_directory(output)
        final_paths = _unique_final_paths(specs, output_dir)
        credentials = _auth.media_session_credentials()

        credential, first = _stage_first(
            specs[0],
            final_paths[0],
            credentials,
            byte_limit=min(MAX_ATTACHMENT_BYTES, MAX_TOTAL_BYTES),
        )
        staged.append(first)
        total = first.size_bytes

        for spec, final_path in zip(specs[1:], final_paths[1:], strict=True):
            remaining = MAX_TOTAL_BYTES - total
            if remaining <= 0:
                raise AttachmentDownloadError(
                    "ATTACHMENT_LIMIT_EXCEEDED",
                    "Selected attachment bytes exceeded the command's documented total safety limit",
                    error_class="conflict",
                )
            try:
                item = _stage_one(
                    spec,
                    final_path,
                    credential,
                    byte_limit=min(MAX_ATTACHMENT_BYTES, remaining),
                )
            except urllib.error.HTTPError as exc:
                safe_error = _safe_http_error(exc)
                if exc.fp is not None:
                    exc.close()
                raise safe_error from exc
            staged.append(item)
            total += item.size_bytes

        _commit_staged(staged)
        selected_account = account or _config.email_account()
        return ok(
            command,
            {
                "thread_id": thread_id,
                "account_email": selected_account,
                "output_directory": str(output_dir),
                "attachment_count": len(staged),
                "total_bytes": total,
                "attachments": [
                    {
                        "message_id": item.spec.message_id,
                        "attachment_id": item.spec.attachment_id,
                        "filename": item.final_path.name,
                        "path": str(item.final_path),
                        "media_type": item.spec.media_type,
                        "response_media_type": item.response_media_type,
                        "inline": item.spec.inline,
                        "size_bytes": item.size_bytes,
                        "sha256": item.sha256,
                    }
                    for item in staged
                ],
            },
        )
    except AttachmentDownloadError as exc:
        for item in staged:
            item.temporary_path.unlink(missing_ok=True)
        return fail(
            command,
            [error(exc.error_class, exc.code, exc.retryable, exc.hint)],
        )
    except (
        TimeoutError,
        ConnectionError,
        urllib.error.URLError,
        http.client.HTTPException,
    ):
        for item in staged:
            item.temporary_path.unlink(missing_ok=True)
        return fail(
            command,
            [
                error(
                    "network",
                    "ATTACHMENT_DOWNLOAD_INTERRUPTED",
                    True,
                    "The attachment transfer was interrupted; no partial output was committed",
                )
            ],
        )
    except Exception as exc:
        for item in staged:
            item.temporary_path.unlink(missing_ok=True)
        return fail(command, [classify_exception(exc)])
