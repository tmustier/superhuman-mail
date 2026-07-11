# Superhuman send authority

This directory owns the public, deployable source for the exact-send authority. It intentionally contains **no production identity, principal, hostname, credential, key, path override, recipient, or customer content**.

## Process boundaries

| Artifact | Fixed API | Runtime secret | Must not possess |
|---|---|---|---|
| `slack-issuer` | `create`, `status`, `cancel`, authenticated Slack decision | Slack signing secret + bot token | Ed25519 private key, Superhuman credential |
| `approval-signer` | semantic `issue` only | Ed25519 private key | Slack secret, Superhuman credential |
| `send-executor` | `execute`, `status`, `abort` | none | Slack secret, Ed25519 private key |
| native credential bridge | `render`, conditional `send` | Superhuman token | Slack secret, signer key |

Each service is a separate signed `.app`, launch identity, Unix socket ACL, state directory, and release pin. The signer and issuer receive their secrets through a Keychain-restricted native launcher and an anonymous stdin pipe. The executor can reach the provider credential only through the separately signed native bridge. No API accepts arbitrary bytes to sign, arbitrary commands, URLs, credentials, trust roots, or raw send payloads.

The shared source module `common/receipt.mjs` implements the merged flat `shm-approval-receipt/v1` wire contract. Release assembly copies and seals that module independently into each artifact; services do not load mutable code from one another.

## Approval and execution flow

1. `shm draft attest-render` creates exact private evidence and a content-free `approval_binding`.
2. `slack-issuer.create` recomputes the binding from complete private evidence, posts that exact evidence through its fixed Slack channel, then stores only the binding, presentation digest, hashed Slack context, nonce, and expiry.
3. A Slack request is accepted only with a valid five-minute request signature, configured principal, exact channel/thread, exact approval keyword, immutable event ID, nonce, and pending state.
4. The issuer commits `approved_waiting_signature`, then asks the signer to construct and sign the flat receipt. A crash safely resumes the same deterministic semantic request.
5. The executor independently verifies the receipt, raw execution identifiers, and a fresh `shm draft get` binding.
6. It durably starts a **minimum 60-second cancellable grace period**. `POST /v1/abort/:receipt_id` wins only while state is `grace`.
7. After grace, it rerenders, compares revision, fingerprint, and every binding field, then atomically changes `grace -> claimed` only if the receipt remains unexpired.
8. The credential bridge invokes `shm draft send --if-revision ... --expected-draft-fingerprint ...`. There is no raw-send verb or retry after an ambiguous provider boundary.
9. Restart converts an interrupted `claimed` row to `unknown`. Replays return the durable row and never POST again.

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

Tests use generated ephemeral keys, synthetic `.test` identities, temporary SQLite databases, and fake providers. They exercise forged/malformed receipts, authenticated context, replay, race, restart, wrong binding, grace abort, expiry at claim, conditional re-render, and body-free audit.

## Production activation gate

Do not install, provision credentials, load launchd services, pin a production approver/root, or send mail from an unreviewed checkout. Activation requires, in order:

1. full Python and authority suites plus strict native/build checks;
2. exact-head public-source scrub;
3. credential-free issuer → signer → 60-second executor E2E with a fake provider;
4. clean independent GPT-5.6 Sol xhigh exact-head P0/P1/P2/P3 review;
5. separately signed release artifacts and recorded hashes/designated requirements;
6. offline provisioning of three identities, four ACLs, roots, and secrets;
7. staged launch with the provider bridge disabled, then render-only validation;
8. one separately approved synthetic sink test only after all previous gates.

See [`docs/operations.md`](docs/operations.md) for rollout, rotation, rollback, and revocation.
