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

- Broker callers → issuer semantic-create/status/cancel socket and executor execute/status/abort socket; never prepare.
- Issuer private group → signer socket and executor prepare socket; never execute.
- Executor private UID → provider bridge executable and executor state; no Slack/signer secret.
- Issuer native launcher → Slack app-token/bot-token bundle only.
- Signer native launcher → Ed25519 private-key item only.
- Credential bridge designated requirement → Superhuman token item only.
- Executor daemon → credential bridge execute permission; no direct Keychain item access.
- Worker identity → none of the secret items, signer/preparer sockets, bridge executable, root policy/runtime config, trusted-prepared markers, or state databases.

The policy directory chain and content-addressed release parents are root-owned and non-writable. Root-owned exact runtime configs under `authority/release/runtime-config/*.in` pin every helper's expected UID and all semantic configuration; caller environment is ignored. Each service state directory is owned by its distinct service UID at `0700`; files are `0600`.

A root installer creates setgid runtime directories before launch: issuer socket `(owner=issuer, group=broker)`, signer socket `(owner=signer, group=issuer-private)`, render-preparer socket `(owner=executor, group=issuer-private)`, and execute socket `(owner=executor, group=broker)`. Use mode `2710` or stricter with no group write/other access; sockets inherit the narrow peer group and are `0660`. Service launchd primary groups remain their private groups, so no supplementary boundary-joining groups are needed. Daemons verify directory owner/group/mode before unlink/bind. Native helpers verify expected UID, root config, and executable parent chain before Keychain access.

## Staged rollout

1. Build from a clean reviewed commit; verify three archives on a separate machine.
2. Create three distinct service UIDs/private groups, the broker group, service-owned `0700` state directories, the four setgid runtime directories above, and a root-owned non-writable policy directory. Render launchd/runtime templates without committing output.
3. Install artifacts into a content-addressed release directory; verify signatures and hashes after copy; atomically select `current`.
4. Install the four exact root-owned runtime configs plus `/Library/Application Support/superhuman-mail/policy/send-executor-trust.json`. Its schema is `{callerGid,preparerGid,roots:[{issuer,keyId,publicKeyPem,allowedApprovers}]}` and accepts at most two roots. Validate expected UIDs, directory GIDs/modes, no worker traversal of release/service-private parents, and direct worker invocation failure before provisioning private material.
5. Bootstrap signer with no key and confirm fail-closed behavior; boot it out.
6. Provision signer key with a Keychain ACL for the signer launcher only. Start signer, then issuer with its own secret and ACL.
7. Start executor without provider credential. Prove broker cannot connect to prepare, issuer cannot connect to execute, and all render/send attempts fail closed.
8. Provision provider credential with a designated-requirement ACL for the signed bridge only. Verify raw worker and executor-daemon Keychain reads fail.
9. Validate trusted prepare → exhaustive Slack presentation → signer → execute against a fake provider, including `shm executor status/abort`, a real 60-second abort, and restart during grace.
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
