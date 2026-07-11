# Authority threat model

## Adversary

Assume the unattended worker controls API arguments, files in its workspace, environment variables, timing, process crashes, concurrency, draft mutations, and attempted replay. It may read this public source. It must not control root-owned policies/artifacts, code-signing identities, service ACLs, Keychain ACLs, authenticated Slack transport, or provider evidence.

## Protected outcomes

- No send without one fresh authenticated approval of the exact rendered payload and envelope.
- One receipt can claim at most one provider POST.
- A human can abort for at least 60 seconds after approval.
- Crash, timeout, or lost response never becomes a retry or false delivery claim.
- Slack secret, signer key, and provider credential never coexist in a process or store.
- Audit is useful without retaining message content or identifiers.

## Fail-closed controls

Strict schemas reject extra fields. The issuer accepts semantics only and obtains evidence from an issuer-only executor prepare socket backed by the signed credential bridge; worker records/screenshots are never authority. Raw Python identity bytes bind floats/Unicode without cross-runtime reserialization, and exact `compose`/`outgoing` PNG roles plus attachment digests are presented. The flat receipt binds account, thread, draft, outgoing fingerprint/payload, recipient envelope, renderer, screenshots, send identity, delay, and schedule. App-token-authenticated Socket Mode fixes team/app/channel/thread, immutable event ID, and principal; a valid decision is durable before acknowledgement. Native secret helpers pin effective UID and root-owned config/executable chains before Keychain access. The signer exposes semantic issue only. The executor verifies trusted-prepared state, starts durable grace, rerenders twice, atomically claims once, and conditionally sends. Unknown outcomes remain unknown permanently.

## Residual risks

Superhuman exposes no documented global idempotency key or compare-and-set send API. Exactly-once is therefore limited to the single canonical executor journal and credential authority; native UI or another credential holder can bypass it. Renderer build drift fails closed but requires reviewed allowlist maintenance. Root or signing-identity compromise defeats local boundaries. Provider/client rendering equivalence still requires a controlled test mailbox and cannot be inferred from screenshots alone.
