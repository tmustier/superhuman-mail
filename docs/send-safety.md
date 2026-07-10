# Send lifecycle, exact render attestation, and reconciliation

`shm` v0.3 fails closed around three distinct questions:

1. **Is this source draft active and valid?**
2. **Are the exact live-Superhuman outgoing bytes the ones a person approved?**
3. **Did an immutable provider message linked to this attempt actually appear?**

A draft timestamp, `DRAFT`/`SENT` presentation label, send-job acceptance, or HTTP 2xx is not by itself proof that mail left the account.

## Canonical lifecycle

```text
active_draft
scheduled
send_requested
send_pending_undo
send_failed
send_aborted
discarded
sent_backend_confirmed
sent_provider_confirmed
inconsistent
```

Only `sent_provider_confirmed` sets both `sent: true` and `outbound_evidence: true`. It requires an immutable provider `SENT` message linked through the completed job's exact message ID, or through both source-draft and persisted Superhuman attempt IDs. A nearby sent message on the same thread never qualifies.

Source drafts may retain a raw `DRAFT` label after sending. Conversely, Superhuman may overlay `SENT` while an optimistic, undoable, or failed job exists. Use the classifier, not either label in isolation.

Read lifecycle without sending:

```bash
shm draft status THREAD --draft-id DRAFT --account owner@example.com
shm draft read THREAD --active-only --account owner@example.com
shm send status THREAD DRAFT --account owner@example.com --wait 120
```

## Strict workflow

### 1. Metadata/lifecycle preflight

```bash
shm send --dry-run THREAD DRAFT --account owner@example.com
```

This is read-only. A successful metadata preflight still returns:

```json
{
  "sendable": true,
  "send_eligible": false,
  "render_attested": false
}
```

It rejects terminal, discarded, already pending/scheduled, contradictory, invalid-recipient, wrong-account, and empty-body drafts. Empty subjects require explicit binding in an exact attestation.

### 2. Open the exact draft in a CDP-enabled Superhuman app

The renderer adapter never navigates or types. The exact draft must already be visible and clean in Superhuman. On macOS, launch the app with CDP only after safely closing any existing instance:

```bash
open -a "Superhuman" --args --remote-debugging-port=9222
```

A Pi/macOS automation caller may supply the visible Superhuman `windowId` for native screenshot fallback when Electron's `Page.captureScreenshot` stalls.

### 3. Create exact render attestation

```bash
shm draft attest-render THREAD DRAFT \
  --account owner@example.com \
  --output ./private-preview \
  --cdp-url http://127.0.0.1:9222 \
  --window-id WINDOW_ID \
  --delay 20
```

The adapter:

- locates the exact live `DraftModel`, editor, account, and view state through React fibers;
- requires a non-dirty model matching the server source snapshot;
- reads `getHTMLSafe()` from Superhuman's editor;
- uses an ephemeral clone and Superhuman's live `MessageModel.getDraftHtmlBody()` path, which invokes `OutgoingMessage.fromDraft()` / `BodyContent.generateForOutgoingMessage()`;
- reserves one `superhuman_id` for approval, second probe, POST, and reconciliation;
- constructs the allowlisted build's exact `toJsonRequest()` envelope contract around those renderer-produced HTML bytes;
- rejects inline signature uploads that cannot be materialized read-only;
- captures the compose view and a network-disabled rendering of the exact outgoing HTML;
- observes network requests and rejects prohibited send/write/upload/comment/cancel/postpone traffic;
- re-reads the server snapshot and history after rendering;
- signs an expiring canonical artifact with a Keychain-held HMAC key.

The outgoing screenshot is supporting evidence for the exact transport HTML. It is **not** a claim about Gmail, Outlook, or another recipient client's CSS. Controlled test-mailbox transport remains required before claiming recipient-client equivalence.

The CLI summary does not print message content, recipient addresses, subject, signature content, provider-user ID, or reserved send identity. The full private artifact is written under a `0700` directory as a `0600` file.

Inspect safely by ID or path:

```bash
shm attestation show ID_OR_PATH \
  --account owner@example.com \
  --thread-id THREAD \
  --draft-id DRAFT
```

The inspector verifies canonical ID/HMAC, screenshot hashes, expiry, and optional binding. It exposes counts, booleans (including `editor_normalized_changed`), renderer versions, screenshot hashes/paths, and the overall fingerprint only. Valid-but-expired artifacts return `usable: false`; tamper or binding mismatch fails.

### 4. Grace period and strict execution

An external gate displays the safe attestation summary, records an opaque approval reference, and runs its grace period. It then invokes:

```bash
shm send --confirm THREAD DRAFT \
  --account owner@example.com \
  --attestation ID_OR_PATH \
  --approval-ref OPAQUE_REFERENCE \
  --delay 20 \
  --wait 120
```

Immediately before POST, `shm`:

1. reruns authoritative lifecycle/envelope/body preflight;
2. verifies account, artifact signature, expiry, thread/draft, approved delay, and source history;
3. creates or resumes one local attempt identity;
4. runs a second no-write live-Superhuman renderer probe with the reserved ID;
5. requires the complete exact fingerprint and outgoing payload bytes to equal approval;
6. atomically claims the only local POST before network I/O;
7. posts the freshly probed exact payload;
8. reconciles userdata/provider evidence without minting another identity.

There is no unattested fallback for customer mail.

## Result and exit contract

Send/status data includes:

```text
state, accepted, sent, provider_confirmed, outbound_evidence
attempt_id, attestation_id, superhuman_id, provider_message_id
idempotency_scope, lifecycle
```

Exit codes:

- `0`: immutable provider-confirmed send, or an explicitly scheduled job;
- `1`: rejected, tampered/stale attestation, terminal failure, or lifecycle inconsistency;
- `4`: accepted/pending/unknown/backend-only after the requested wait.

Follow-up/CRM automation must consume only `provider_confirmed: true` / `state: sent_provider_confirmed`.

## Idempotency scope

The SQLite attempt journal lives in one canonical private per-user state directory. A unique `(immutable_provider_user_id, draft_id)` row and `BEGIN IMMEDIATE` claim guarantee one POST for cooperating `shm` processes sharing that journal.

This does **not** serialize another host, another state directory, the native Superhuman UI, or an uncooperative caller. Outputs therefore say:

```text
idempotency_scope: local_cooperating_processes
```

Global exactly-once requires a vendor idempotency key or compare-and-set send contract.

After a network timeout, reset, or HTTP conflict, `shm` reconciles the same attempt. It never automatically posts again after the pre-network claim. An unresolved outcome remains `unknown`/pending and exits 4.

## Private state and test controls

Production HMAC keys are created/read from macOS Keychain service `superhuman-mail-attestation-v1`.

These environment variables exist for controlled rollout/testing:

- `SHM_STATE_DIR`
- `SHM_RENDERER_CDP_URL`
- `SHM_RENDERER_WINDOW_ID`
- `SHM_RENDERER_ALLOW_BUILDS` (`APP_VERSION@WEB_VERSION`; production rollout override)
- `SHM_RENDERER_ALLOW_VERSIONS` (web-only fixture/testing override)
- `SHM_ATTESTATION_KEY` (tests only; do not use as a production secret path)

A new Superhuman code version fails closed until its renderer contract and non-sending E2E are reviewed.
