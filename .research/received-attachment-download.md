# Received attachment download route

Date: 2026-08-18

## Finding

The Superhuman Electron web bundle exposes two relevant paths for Gmail attachments:

1. Its provider adapter requests Gmail attachment data from `content.googleapis.com/gmail/v1/users/me/messages/<messageId>/attachments/<attachmentId>` using OAuth held inside the app.
2. Its attachment model builds a browser-facing URL at `https://media.superhuman.com/v2/attachments/<providerId>/<messageId>/<attachmentId>`.

The second route is the clean CLI seam. The desktop app stores a separate encrypted cookie for each signed-in provider identity on the exact `media.superhuman.com` host. The cookie name is the provider ID used in the route. Anonymous GETs and the existing private-API bearer token both returned 401; the matching host-scoped media cookie returned the attachment bytes.

## Local metadata seam

Received-message attachment metadata already exists in each account's local thread JSON. The required fields are:

- `messageId`
- `attachmentId`
- `name`
- `type`
- `size`
- `inline`

The previous normalized `thread messages` projection retained filename/type/size but discarded the identifiers required to fetch bytes. The downloader therefore reads raw thread JSON internally while keeping the existing public message projection unchanged. This makes local message metadata a real coverage boundary: attachment bytes need not be cached or previously opened, but a thread the desktop app has not synced into its local store cannot be addressed and fails explicitly as `THREAD_NOT_IN_LOCAL_CACHE`.

## Safety decisions

- Never emit cookie values or provider IDs used only for route authentication.
- Keep the media cookie on the exact media host and remove it on allowed cross-host redirects.
- Reject redirects outside the established Superhuman/Google/Firebase attachment host set.
- Stream rather than buffering entire files.
- Enforce 512 MiB per-file and 1 GiB per-command limits.
- Verify non-zero cached sizes and compute SHA-256 for every output.
- Stage every file first, then atomically link final paths without overwrite.
- Sanitize untrusted filenames and create private `0700` directories / `0600` files.
- Resolve multi-account identity by trying only signed-in local media sessions against the selected account's attachment metadata.

## Validation

- Two live received PDFs downloaded through the Superhuman media route.
- Both outputs had the expected media type, exact cached byte count, and valid PDF bytes.
- Independent Gmail API downloads of the same attachments were byte-for-byte identical.
- An unread message from a second signed-in account downloaded five same-message attachments through `--message-id`: mixed PNG/JPEG, four inline and one ordinary attachment, with cached and response media types matching.
- A 6,866,388-byte unread PDF from that second account downloaded through exact `--attachment-id`, exercising repeated 1 MiB stream reads.
- Unit coverage includes identity fallback, secret redaction, 401/404 classification, interrupted-read cleanup, HTML error-document rejection, size mismatch cleanup, no-overwrite behavior, missing/exact selectors, deterministic collisions, filename traversal, source validation, byte limits, redirect handling, auth extraction, CLI dispatch, and Python client exposure.
