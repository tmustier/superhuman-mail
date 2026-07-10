# External approval receipt issuer contract

Status: **core verifier implemented; external issuer not yet deployed**  
Receipt schema: `shm-approval-receipt/v1`  
JSON Schema: [`approval-receipt-v1.schema.json`](approval-receipt-v1.schema.json)

## Security boundary

The unattended worker is untrusted. It may choose CLI arguments, environment variables, paths, payloads, and process timing. It must not possess:

- the receipt issuer's Ed25519 private key;
- a writable approval trust root;
- Superhuman write/session credentials outside the trusted executor;
- a local/raw transport fallback that bypasses receipt verification;
- an API that signs caller-provided bytes.

The issuer belongs in `gugu91/extensions`' `slack-bridge` / durable `broker-core` state, under a separate hardened principal or service. Core `superhuman-mail` contains verification and atomic consumption only. Until a trust root is release-pinned or installed as a root-owned non-writable system file, `shm send --confirm` fails with `APPROVAL_TRUST_UNAVAILABLE`.

A signed receipt is necessary but not sufficient if the worker retains raw Superhuman credentials. Production must run receipt verification, atomic consumption, and transport in a broker-owned/root-owned or remote trusted executor. The worker may submit a request to that executor but cannot patch it or extract its credentials.

## Issuer API shape

The trusted broker exposes semantic operations only:

1. `approval.create(pending_request)`
2. `approval.status(request_id)`
3. `approval.cancel(request_id)`

It never exposes `sign(bytes)`, private-key export, arbitrary approver selection, or caller-controlled trust-root configuration.

`approval.create` accepts the content-free `approval_binding` returned by:

```bash
shm attestation show ID_OR_PATH --account EMAIL --thread-id THREAD --draft-id DRAFT
```

The binding covers:

- action and provider;
- immutable account, account email, thread, and draft hashes;
- attestation ID and exact outgoing fingerprint;
- complete outgoing-payload hash;
- From/To/Cc/Bcc envelope hash;
- renderer-build and screenshot-set hashes;
- reserved send-identity hash;
- delay and scheduled-send hash.

The broker records the request, random nonce, ≤5-minute expiry, expected Thomas Slack principal, and approval presentation atomically before prompting. The human presentation must show the complete intended recipients, subject, body/render, attachments, and scheduling behavior. Uploaded/rendered evidence must hash to the server-recorded pending request; caller labels or summaries are not authoritative.

## Authenticated decision

The issuer directly consumes an authenticated Slack event from Thomas (`U0AF5S3LQ5C`) tied to one pending request. It must verify workspace, channel/thread, Slack event/message ID, principal, exact approval keyword, pending state, nonce, and expiry. It then atomically changes `pending -> approved` once and signs only the server-constructed receipt.

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
  "principal": "slack:U0AF5S3LQ5C",
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

Core verifies schema, canonical ID, pinned issuer/key, Ed25519 signature, authorized approver, not-before/expiry/max TTL, action/provider, and byte-exact attestation binding. It performs the mandatory second renderer probe. Only then does one SQLite `BEGIN IMMEDIATE` transaction:

1. recheck receipt expiry;
2. reject a previously consumed receipt ID;
3. insert the immutable receipt-consumption row;
4. change the exact attempt from `prepared/post_count=0` to `posting/post_count=1`.

A crash cannot commit receipt consumption without the POST claim or vice versa. After the claim, retry is reconciliation-only. Receipt consumption is retained even when old terminal attempts are purged.

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
