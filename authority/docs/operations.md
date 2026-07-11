# Authority rollout, rotation, and revocation

This is a parameterized runbook. Production values belong in a private, root-owned deployment repository or secret manager—not this public repository.

## Release inputs and evidence

`authority/release/build-release.sh` refuses a dirty checkout, verifies the pinned standalone `shm` reports `shm-executor/v1`, compiles native boundaries, embeds the signed credential-bridge hash, and emits three separate signed archives plus `release-pins.json`.

Record privately for each release:

- exact public source commit and scrub report;
- Node and standalone `shm` hashes;
- issuer, signer, and executor archive hashes, code-directory hashes, designated requirements, and signing certificate chains;
- native bridge hash and Keychain ACL requirement;
- rendered launchd templates and non-secret policy hashes;
- test, E2E, review, and rollback evidence.

Never reuse one signing identity across issuer, signer, and executor. The credential bridge may share the executor release identity but receives a narrower Keychain ACL than the executor daemon.

## Independent ACL matrix

- Broker callers → issuer socket only.
- Issuer identity → signer socket only.
- Approved executor callers → executor socket only.
- Issuer native launcher → Slack app-token/bot-token bundle only.
- Signer native launcher → Ed25519 private-key item only.
- Credential bridge designated requirement → Superhuman token item only.
- Executor daemon → credential bridge execute permission; no direct Keychain item access.
- Worker identity → none of the secret items, signer socket, bridge executable, provider config, trust policy, imported attestations, or state databases.

The public trust-policy directory chain is root-owned and non-writable. Each service state directory is owned by its distinct service UID at `0700`; files are `0600`. A daemon owns its socket and runs with the one peer group as its primary launchd group, allowing `0660` without root or a shared service UID. The credential bridge runs as the executor UID and relies on its executable-designated Keychain ACL, not effective UID 0. Deny supplementary memberships that join boundaries.

## Staged rollout

1. Build from a clean reviewed commit; verify three archives on a separate machine.
2. Create three distinct service UIDs, their peer socket groups, service-owned `0700` state directories, and a separate root-owned non-writable policy directory. Render templates without committing output.
3. Install artifacts into a content-addressed release directory; verify signatures and hashes after copy; atomically select `current`.
4. Install `/Library/Application Support/superhuman-mail/policy/send-executor-trust.json` with one active root tuple and the configured approver principal. The exact schema is `{callerGid, roots:[{issuer,keyId,publicKeyPem,allowedApprovers}]}` and accepts at most two roots. Do not provision private material yet.
5. Bootstrap signer with no key and confirm fail-closed behavior; boot it out.
6. Provision signer key with a Keychain ACL for the signer launcher only. Start signer, then issuer with its own secret and ACL.
7. Start executor without provider credential. Run fake-provider and render-only probes; all send attempts must fail closed.
8. Provision provider credential with a designated-requirement ACL for the signed bridge only. Verify raw worker and executor-daemon Keychain reads fail.
9. Validate issuer → signer → executor against a fake provider, including a real 60-second abort and restart during grace.
10. Separately approve one controlled synthetic sink send. Never broaden recipients during activation.

Installation, credential provisioning, `launchctl bootstrap`, and live send are deliberately not performed by repository tests or release assembly.

## Key and artifact rotation

Use bounded overlap:

1. Build/sign a new signer artifact under its own release hash.
2. Add the new public key as the second allowed root to executor/core trust policy.
3. Start the new signer key ID; direct new issuer decisions to it.
4. Wait for the maximum receipt TTL plus grace and reconciliation window.
5. Revoke the old issuer route, remove the old root, boot out the old signer, then delete its Keychain item.

Never accept more than two roots, caller-supplied roots, environment trust overrides, or an unbounded compatibility window.

## Emergency revocation

In order:

1. Disable issuer `create` and reject new Slack decisions.
2. Boot out the executor to prevent new claims.
3. Remove the provider-token ACL/item.
4. Boot out signer and issuer; remove signer and Slack-secret items.
5. Remove the compromised public root from both core and executor trust policies.
6. Preserve databases and body-free audit immutably for reconciliation.
7. Mark any `claimed` execution without provider confirmation `unknown`; never retry it automatically.
8. Reconcile provider evidence manually before any replacement draft is approved.

Revoking a release selects a previously retained content-addressed artifact only after its signature/hash and still-valid trust policy are reverified. Never roll back by editing a selected release in place.
