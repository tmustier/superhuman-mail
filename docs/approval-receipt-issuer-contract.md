# External approval receipt issuer contract

Status: **core verifier and public authority source implemented; production authority not yet deployed**
Receipt schema: `shm-approval-receipt/v1`  
JSON Schema: [`approval-receipt-v1.schema.json`](approval-receipt-v1.schema.json)

## Security boundary

The unattended worker is untrusted. It may choose CLI arguments, environment variables, paths, payloads, and process timing. It must not possess:

- the receipt issuer's Ed25519 private key;
- a writable approval trust root;
- Superhuman write/session credentials outside the trusted executor;
- a local/raw transport fallback that bypasses receipt verification;
- an API that signs caller-provided bytes.

The issuer, signer, and executor source live under this repository's `authority/` tree and ship as separately signed artifacts under distinct hardened principals and ACLs. Core `superhuman-mail` contains read-only verification plus a thin client; atomic consumption exists only in the executor journal. Until a trust root is release-pinned or installed as a root-owned non-writable system file, `shm send --confirm` fails with `APPROVAL_TRUST_UNAVAILABLE`.

A signed receipt is necessary but not sufficient if the worker retains raw Superhuman credentials. Production must run receipt verification, atomic consumption, and transport in a broker-owned/root-owned or remote trusted executor. The worker may submit a request to that executor but cannot patch it or extract its credentials.

## Issuer API shape

The trusted broker exposes semantic operations only:

1. `approval.create(pending_request)`
2. `approval.status(request_id)`
3. `approval.cancel(request_id)`

It never exposes `sign(bytes)`, private-key export, arbitrary approver selection, or caller-controlled trust-root configuration.

`approval.create` accepts only `{account, thread_id, draft_id, delay_seconds, ttl_seconds}`. It calls the executor's issuer-only prepare socket; caller-provided bindings, records, screenshots, paths, or payload bytes are schema errors. The signed credential bridge runs the allowlisted live renderer and returns raw Python identity bytes plus exactly two role-bound PNGs (`compose`, `outgoing`). The executor validates and marks that evidence trusted-prepared before returning it to the issuer. `shm attestation show` remains observational and is never broker authority.

The binding covers:

- action and provider;
- immutable account, account email, thread, and draft hashes;
- attestation ID and exact outgoing fingerprint;
- complete outgoing-payload hash;
- From/To/Cc/Bcc envelope hash;
- renderer-build and screenshot-set hashes;
- reserved send-identity hash;
- delay and scheduled-send hash.

The issuer independently validates the trusted-preparer bundle, recomputes the binding, posts an exhaustive representation of every reviewed outgoing field plus attachment digests, and uploads both role-bound screenshots to its fixed policy channel. It then records the request, random nonce, ≤5-minute expiry, configured Slack principal, presentation digest, and returned team/app/channel/thread hashes. Prepared evidence that does not match the semantic request fails before presentation.

## Authenticated decision

The issuer directly consumes Slack Socket Mode from its app-token-authenticated WebSocket. It validates configured team ID, app ID, channel/thread, an ordinary unedited human message, immutable Events API `event_id`, principal, exact approval keyword, pending state, nonce, and expiry. The fixed `(team, app, channel, thread)` context resolves the pending request; no HTTP path or proxy selects a request ID. It durably changes `pending -> approved_waiting_signature` before acknowledging the Socket Mode envelope, then signs only the server-constructed receipt. Startup recovery resumes any committed unsigned decision.

Rejections, cancellations, duplicate decisions, late events, edited/deleted approval messages, or events from another principal never produce a receipt. The durable broker state owns TTL, nonce, decision, and audit history.

## Canonical receipt and signature

The receipt has no optional or extra fields. Core canonicalization is UTF-8 JSON with sorted keys, compact separators, and Unicode emitted directly (`ensure_ascii=false`).

1. Build all fields except `receipt_id` and `signature`.
2. `receipt_id = "sha256:" + SHA256(canonical_json(content)).hex()`.
3. Add `receipt_id`.
4. `signature = "ed25519:" + base64url_no_padding(Ed25519.sign(canonical_json(receipt_without_signature)))`.

`issued_at` and `expires_at` are UTC RFC 3339 timestamps. `expires_at - issued_at` must be positive and at most five minutes. `nonce` has at least 128 bits of entropy.

The approver object is:

```json
{
  "principal": "slack:CONFIGURED_APPROVER_ID",
  "approval_event_id": "<immutable authenticated Slack event/message ID>"
}
```

The private key stays in the issuer service. Core pins only its public key and exact `issuer`/`key_id`/allowed-approver tuple. Environment variables and user-writable files cannot add or replace roots.

## Core verification and consumption

Read-only verification:

```bash
shm approval verify RECEIPT.json --attestation ID_OR_PATH
```

Strict send:

```bash
shm send --confirm THREAD DRAFT \
  --account EMAIL \
  --attestation ID_OR_PATH \
  --approval-receipt RECEIPT.json \
  --cdp-url http://127.0.0.1:9222 \
  --window-id WINDOW_ID \
  --wait 120
```

Agent-facing core submits only receipt plus account/thread/draft identifiers to the broker-only execute socket. The executor re-verifies the receipt/binding, its own trusted-prepared marker/artifacts, and the mandatory renderer probe. Its one canonical SQLite journal starts the 60-second abort grace and later uses `BEGIN IMMEDIATE` to recheck expiry and atomically move `grace -> claimed` once. There is no local receipt-consumption journal, caller evidence import, or desktop transport in `shm send --confirm`. `shm executor status|abort RECEIPT_ID` exposes canonical grace/reconciliation control.

A crash cannot create a second claim. After `claimed`, retry is reconciliation-only; interrupted claims become permanently `unknown`. Definitive pre-claim failures become durable `failed` or `expired` rows.

## Typed result contract

Before receipt verification:

```json
{
  "approval_authority": "external_receipt_required",
  "approval_verified": false,
  "approval_consumed": false,
  "unattended_send_eligible": false
}
```

For a verified attempt:

```json
{
  "approval_authority": "external_ed25519_receipt_v1",
  "approval_verified": true,
  "approval_consumed": true,
  "approval_receipt_id": "sha256:...",
  "approval_issuer": "...",
  "approval_key_id": "...",
  "unattended_send_eligible": false,
  "trusted_executor_required": true
}
```

`sent: true` still requires independent provider confirmation. Receipt verification authorizes exactly one attempt; it does not prove delivery.

## Required adversarial proof before rollout

- caller-invented opaque ref cannot authorize;
- absent/unpinned/caller-substituted trust roots fail;
- forged signature and unknown issuer/key fail;
- unauthorized Slack principal/event fails at issuer and verifier;
- edited receipt or extra fields fail;
- wrong account/thread/draft/action/provider fail;
- changed recipient, body, attachment, renderer, screenshot, delay, schedule, or send identity fail;
- future, expired, zero/negative, and >5-minute lifetimes fail;
- sequential and concurrent replay fail;
- receipt expiry between verify and claim fails inside the transaction;
- signal/crash cannot split consume from claim;
- worker process descendants cannot access issuer key or Superhuman transport credentials;
- only the exact approved synthetic test reaches one POST and provider-confirmed completion.
