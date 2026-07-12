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

The renderer adapter never navigates or types. It accepts loopback-local CDP endpoints only. The exact draft must already be visible and clean in Superhuman. On macOS, launch the app with CDP only after safely closing any existing instance:

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
- mirrors the current build's reminder behavior: reminder stays on the persisted, fingerprint-bound draft and is not copied into `toJsonRequest()`;
- rejects inline signature uploads that cannot be materialized read-only;
- captures the compose view and a network-disabled rendering of the exact outgoing HTML;
- requires the exact draft model to already be visible and never focuses or navigates the app;
- enables CDP Fetch interception and target-offline mode before render work, aborting every non-allowlisted non-idempotent request before dispatch and failing the attestation if the app attempted one;
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

The inspector verifies canonical ID/HMAC, screenshot hashes, expiry, and optional binding. It exposes counts, booleans (including `editor_normalized_changed`), renderer versions, screenshot hashes/paths, the overall fingerprint, and a content-free `approval_binding`. Valid-but-expired artifacts return `usable: false`; tamper or binding mismatch fails.

### 4. Grace period and strict execution

The external Slack approval broker accepts only account/thread/draft/delay semantics, obtains a trusted-prepared render over the issuer-only executor socket, presents every reviewed outgoing field plus attachment digests and both role-bound screenshots, authenticates the authorized Socket Mode decision, and issues a ≤5-minute Ed25519 receipt. Then submit it to the credential-isolated executor:

```bash
# Optional local inspection only when the trusted-prepared artifact is available:
# shm approval verify RECEIPT.json --attestation ID_OR_PATH
shm send --confirm THREAD DRAFT \
  --account owner@example.com \
  --approval-receipt RECEIPT.json \
  --delay 20 \
  --wait 120
```

`shm send --confirm` performs no provider call and sends no evidence bytes. It submits the receipt and identifiers over the broker-only execute socket. The executor then:

1. re-verifies the receipt against its own trusted-prepared marker, record, screenshot roles/bytes, and fixed storage (never a caller path);
2. runs authoritative lifecycle/envelope/body preflight and a no-write live renderer probe;
3. durably starts a minimum 60-second abort grace, requiring enough receipt lifetime for grace plus claim margin;
4. reruns the renderer after grace and requires complete revision, fingerprint, binding, and outgoing-payload equality;
5. in one `BEGIN IMMEDIATE`, rechecks expiry and atomically changes the only receipt row from `grace` to `claimed`;
6. invokes the credential bridge's conditional provider call once;
7. reconciles provider evidence without retry after any ambiguous boundary, then removes private prepared records/screenshots while retaining body-free journal hashes.

There is no unattested fallback for customer mail.

## Approval authority boundary

Caller-controlled `--approval-ref` is deprecated correlation and never authorizes. Before external verification, results say `approval_authority: external_receipt_required`, `approval_verified: false`, and `unattended_send_eligible: false`. A verified receipt changes authority to `external_ed25519_receipt_v1`; consumption state is authoritative only in the isolated executor journal, never in local inspection state.

Environment variables and user-writable files cannot install receipt trust roots. Roots are release-pinned or loaded from `/Library/Application Support/superhuman-mail/approval-trust-v1.json` only when it is a root-owned regular file not writable by group/others. With no root, confirm fails `APPROVAL_TRUST_UNAVAILABLE`.

Verification alone does not isolate transport credentials. Production runs receipt consumption and the provider call inside one canonical durable executor under its own service UID; only the separately signed credential bridge can read the provider token. The unattended worker must not possess that credential, the issuer private key, a writable trust root, or a local/raw transport fallback. See [`approval-receipt-issuer-contract.md`](approval-receipt-issuer-contract.md).

## Result and exit contract

Send/status data includes:

```text
state, post_claimed, accepted, sent, provider_confirmed, outbound_evidence
attempt_id, attestation_id, superhuman_id, provider_message_id
approval_authority, approval_verified, approval_consumed, approval_receipt_id
approval_issuer, approval_key_id, unattended_send_eligible, trusted_executor_required
idempotency_scope, lifecycle
```

Exit codes:

- `0`: immutable provider-confirmed send, or an explicitly scheduled job;
- `1`: rejected, tampered/stale attestation, or definitely terminal failure;
- `4`: pending/unknown/backend-only or inconsistent possible-send evidence after the requested wait.

Follow-up/CRM automation must consume only `provider_confirmed: true` / `state: provider_confirmed` from the authority result.

## Idempotency scope

The executor SQLite journal is the only receipt-consumption journal. `receipt_id` is its primary key; immutable receipt/execution/binding hashes detect conflicting replay, and `BEGIN IMMEDIATE` provides one `grace -> claimed` transition.

This does **not** serialize another credential authority or the native Superhuman UI. Production therefore permits transport credentials in only one canonical executor/bridge boundary.

Global exactly-once requires a vendor idempotency key or compare-and-set send contract.

`post_claimed` means the local exactly-once journal irrevocably claimed its single POST slot. `accepted` means HTTP 2xx or later backend/provider lifecycle evidence proves the server accepted the request; a pre-connect/network exception can therefore be `post_claimed: true, accepted: false`.

After a network timeout, reset, or HTTP conflict, `shm` reconciles the same attempt. It never automatically posts again after the pre-network claim. An unresolved outcome remains `unknown`/pending and exits 4.

## Private state and test controls

Production HMAC keys are created/read from macOS Keychain service `superhuman-mail-attestation-v1`.

These environment variables exist for controlled rollout/testing:

- `SHM_STATE_DIR`
- `SHM_RENDERER_CDP_URL`
- `SHM_RENDERER_WINDOW_ID`

A new Superhuman code version fails closed until its renderer contract and non-sending E2E are reviewed.
