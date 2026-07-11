# Superhuman send authority

This directory owns the public, deployable source for the exact-send authority. It intentionally contains **no production identity, principal, hostname, credential, key, path override, recipient, or customer content**.

## Process boundaries

| Artifact | Fixed API | Runtime secret | Must not possess |
|---|---|---|---|
| `slack-issuer` | `create`, `status`, `cancel`, authenticated Socket Mode decision | Slack app token + bot token | Ed25519 private key, Superhuman credential |
| `approval-signer` | semantic `issue` only | Ed25519 private key | Slack secret, Superhuman credential |
| `send-executor` | issuer-only `prepare`; broker-only `execute`, `status`, `abort` on a separate socket | none | Slack secret, Ed25519 private key |
| native credential bridge | trusted `prepare`, `render`, conditional `send` | Superhuman token | Slack secret, signer key |

Each service is a separate signed `.app`, launch identity, Unix socket ACL, state directory, and release pin. Before Keychain access, every native helper verifies a root-owned exact runtime config, its release parent chain, and a pinned effective UID. The signer and issuer stream secrets through anonymous stdin pipes; the executor reaches the provider credential only through the separately signed native bridge. No API accepts arbitrary bytes to sign, caller evidence, arbitrary commands, URLs, credentials, trust roots, or raw send payloads.

The shared source module `common/receipt.mjs` implements the merged flat `shm-approval-receipt/v1` wire contract. Release assembly copies and seals that module independently into each artifact; services do not load mutable code from one another.

## Approval and execution flow

1. `slack-issuer.create` accepts only account/thread/draft/delay semantics and calls the executor's issuer-only prepare socket. Caller evidence bytes are rejected.
2. The signed credential bridge runs the allowlisted live renderer under the executor UID, returns raw Python identity bytes plus exactly `compose` and `outgoing` PNGs, and the executor durably marks that ID as trusted-prepared. The issuer recomputes the binding, posts every reviewed outgoing field plus attachment digests, uploads both screenshots, then retains only binding/presentation/context hashes, nonce, and expiry.
3. The issuer consumes Slack Socket Mode directly. It validates fixed team/app/channel, ordinary unedited human thread-message shape, configured principal, exact keyword/nonce, immutable `event_id`, and pending state. The decision is committed before the Socket Mode acknowledgement; no proxy or caller-selected request ID participates.
4. The issuer commits `approved_waiting_signature`, acknowledges Slack, then asks the signer to construct and sign the flat receipt. Startup recovery resumes the same semantic signing request after a crash.
5. Agent-facing confirm submits only receipt plus account/thread/draft identifiers. The executor independently verifies the receipt, execution identifiers, its own trusted-prepared marker/artifacts, and a fresh `shm draft get` binding. Execution accepts only the receipt-bound attestation ID, never a caller path or bundle.
6. The prepare and execute APIs use separate service-owned setgid runtime directories and peer groups; the issuer cannot reach execute, and the broker cannot inject prepare evidence.
7. It durably starts a **minimum 60-second cancellable grace period**. `POST /v1/abort/:receipt_id` wins only while state is `grace`.
8. After grace, it rerenders, compares revision, fingerprint, and every binding field, then atomically changes `grace -> claimed` only if the receipt remains unexpired. Definitive pre-claim failures transition durably to `failed` or `expired`.
9. The credential bridge invokes `shm draft send --if-revision ... --expected-draft-fingerprint ...`. Agent-facing `shm send --confirm` is only a Unix-socket client to this journal; there is no second local claim or raw-send path.
10. Restart converts an interrupted `claimed` row to `unknown`. Replays return the durable row and never POST again.

Audit tables contain receipt/binding hashes, state, timestamps, and bounded error codes only—never body, subject, recipient, token, signature, provider response, or provider message ID.

## Local credential-free checks

```bash
python3 -m pytest -q
npm run test:authority
bash authority/release/public-source-scrub.sh
bash -n authority/release/*.sh
swiftc -typecheck authority/slack-issuer/native/RuntimeSecretLauncher.swift
swiftc -typecheck authority/approval-signer/native/RuntimeSecretLauncher.swift
swiftc -typecheck authority/send-executor/native/CredentialBridge.swift
```

Tests use generated ephemeral keys, synthetic `.test` identities, temporary SQLite databases, and fake providers. Unit tests advance a virtual clock across grace; they do not claim a real-time production E2E. Cross-runtime tests cover raw Python identity bytes containing floats/non-BMP keys, required PNG roles, semantic-only preparation, direct secret-helper rejection, and executor-prepared rerender without a worker HMAC. Live Socket Mode, launchd users/ACLs, a real 60-second wait, native bridge execution, and provider behavior remain activation checks.

## Production activation gate

Do not install, provision credentials, load launchd services, pin a production approver/root, or send mail from an unreviewed checkout. Activation requires, in order:

1. full Python and authority suites plus strict native/build checks;
2. exact-head public-source scrub;
3. credential-free issuer → signer → executor contract test with a fake provider and separately labelled virtual grace clock;
4. clean independent GPT-5.6 Sol xhigh exact-head P0/P1/P2/P3 review;
5. separately signed release artifacts and recorded hashes/designated requirements;
6. offline provisioning of three identities, four ACLs, roots, and secrets;
7. staged launch with the provider bridge disabled, then render-only validation;
8. one separately approved synthetic sink test only after all previous gates.

See [`docs/operations.md`](docs/operations.md) for rollout, rotation, rollback, and revocation.
