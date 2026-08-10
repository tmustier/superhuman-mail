# Changelog

## Unreleased

- add the versioned, bounded, privacy-aware `shm reader scan` local-cache contract with secure transient snapshots and deterministic multi-account results
- make the `shm` launcher self-contained with `uv` and install it on PATH through `scripts/setup.sh`, removing dependence on an activated repository virtualenv
- update the declared `cryptography` floor to 49.0.0
- select Superhuman account cookies by the returned email and Google ID, not HTTP success alone
- move public exact-send authority ownership into this repository
- add separately signed Slack issuer, Ed25519 signer, and 60-second cancellable send executor artifacts with independent ACLs and revocation
- add the native provider credential bridge and fixed `shm executor-contract`, `draft get`, and conditional `draft send` contract
- replace the v0.3.0 local confirm/receipt journal with a thin client to one credential-isolated executor journal
- add executor-owned trusted preparation, raw portable `sha256:` identity bytes, exhaustive Slack presentation with two role-bound PNGs/attachment digests, and fixed authenticated Socket Mode decision ingress
- pin every secret-bearing native helper to root-owned runtime config, executable parent safety, and its expected service UID before Keychain access
- add durable grace/abort/replay/crash semantics, body-free authority audit, adversarial tests, and exact-head public-source scrub
- parameterize all production identity, policy, and deployment values; no credentials or personal/customer data are committed

## v0.3.0 — 2026-07-10

This safety release replaces ambiguous draft/send behavior with typed lifecycle, local attempt reconciliation, and exact live-Superhuman render attestation.

### Breaking send changes

- `shm send --confirm` now requires `--account`, `--attestation`, and an externally signed `--approval-receipt`; caller-supplied `--approval-ref` never authorizes
- HTTP acceptance no longer returns unconditional `sent: true`
- pending/backend-only/unknown/inconsistent possible-send outcomes exit `4`; only provider-confirmed immediate sends return sent success
- customer sends have no raw-HTML or unattested fallback

### Added

- canonical lifecycle/provenance classifier joining source wrappers, send jobs, and immutable provider messages
- `shm draft status`, lifecycle-aware `draft read --active-only`, and `shm send status`
- mandatory execute-time rejection for terminal, discarded, pending/scheduled, inconsistent, invalid-recipient, wrong-account, and empty-body drafts
- private SQLite attempt journal with stable `superhuman_id`, one local POST claim, and reconcile-before-retry behavior
- exact renderer attestation through the live Superhuman DraftModel/editor/BodyContent pipeline
- post-grace second no-write renderer probe and complete payload/source/version/signature/attachment stale binding
- signed, expiring local attestation artifacts and safe `shm attestation show` inspection
- `shm approval verify`, the `shm-approval-receipt/v1` Ed25519 contract, exact content-free approval bindings, and typed issuer/key/receipt state
- atomic single-use receipt consumption in the same SQLite transaction as the only local POST claim
- macOS native-window screenshot fallback for Electron CDP targets
- adversarial tests for terminal DRAFT residue, optimistic SENT overlays, delayed completion, lost responses, local concurrency, stale payloads, attachment verification, privacy, and provider gating

### Safety and scope

- source-draft IDs, labels, timestamps, and HTTP 2xx are never outbound proof
- only an unambiguously linked immutable provider `SENT` message sets `sent: true`
- local idempotency is explicitly scoped to cooperating processes sharing one journal; cross-machine/global exactly-once is not claimed
- full attestation artifacts stay in `0700`/`0600` local storage; CLI inspection redacts message content and stable private provider IDs
- Superhuman app+web renderer builds are code-allowlisted, non-idempotent probe traffic fails closed, and attestation signing keys come only from Keychain
- receipt trust roots cannot come from environment/user-writable files; absent external issuer trust disables confirm

## v0.2.1 — 2026-03-24

This patch release includes all changes merged since `v0.2.0`.

### Highlights

- fixed reply threading so drafts and sends anchor to the real external message instead of Superhuman system/share messages
- added smart-send draft fields for scheduled-send adjacent workflows, including abort-on-reply, reminders, and sensitivity labels
- improved setup for multi-account Superhuman installs and made version extraction more precise
- inlined quoted content in forward drafts to better match Superhuman's visible forward behavior
- tightened Pi package exposure so only `SKILL.md` is shipped to Pi

### Added

- smart-send draft flags across reply / reply-all / forward / compose:
  - `--abort-on-reply`
  - `--reminder`
  - `--sensitivity-label-id`
  - `--sensitivity-tenant-id`
- multi-account account selection during `shm setup`
- focused docs for smart-send workflows, including scheduled-send + attachment flows
- regression tests covering reply threading anchor selection and self-sent follow-up behavior

### Changed

- forward drafts now inline quoted content instead of relying only on separate quoted metadata
- Pi package metadata now exposes only `SKILL.md`
- setup flow is more explicit when multiple signed-in Superhuman accounts are present

### Fixed

- reply drafts no longer thread off `sharing@superhuman.com` / other Superhuman system messages
- follow-ups on self-sent external emails now target the original recipients instead of replying to self
- reply resolution now ignores internal-only forwards when choosing the visible external anchor message
- Superhuman version extraction is now anchored to the `lastCodeVersion` key
- temp SQLite DB copies are written with restrictive permissions
- CLI `emit()` now returns exit codes cleanly for better scripting and testing behavior

### Notes

- This is a patch bump: all changes are backward-compatible improvements, fixes, and additive flags.
- The official Superhuman MCP beta was also investigated further during this cycle, but no MCP-facing API surface is included in this release.

## v0.2.0 — 2026-03-23

This release includes **all changes merged since `v0.1.1`**.

### Highlights

- restructured the `shm` CLI around a clearer, more agent-friendly surface
- added first-class read-receipt / Recent Opens support via `opens`
- aligned the Python client and repo docs with the new command model
- shipped and restored a full set of reverse-engineered Superhuman endpoint docs
- included timezone / recipient fixes and improved skill discoverability from earlier unreleased commits

### Breaking CLI changes

- `shm thread read <thread_id>` → `shm thread messages <thread_id>`
- `shm thread read-statuses <thread_id>` → `shm opens <thread_id>`
- `shm share <thread_id> <draft_id>` → `shm draft share <thread_id> <draft_id>`
- `shm unshare <thread_id> <draft_id>` → `shm draft unshare <thread_id> <draft_id>`

### Added

- top-level `opens` command for per-thread read receipts
- `shm opens --recent` backed by the local `activity_feed` cache
- `--recipient` filtering for both opens modes
- `superhuman_mail/opens.py`
- `python -m superhuman_mail` support via `superhuman_mail/__main__.py`
- deep endpoint docs:
  - AI
  - agent sessions
  - calendar
  - CRM
  - knowledge/links/containers
  - labels
  - messages/send
  - read statuses / Recent Opens
  - teams/misc

### Changed

- `thread userdata` is now explicitly documented as advanced/raw
- Python client now exposes:
  - `client.thread.messages(...)`
  - `client.opens.per_thread(...)`
  - `client.opens.recent(...)`
  - `client.draft.share(...)`
  - `client.draft.unshare(...)`
- backward-compatible Python client aliases were kept for older callers where practical
- README and SKILL documentation were rewritten to match the shipped command surface

### Fixed

- `emit()` now supports explicit exit-code overrides
- `thread list --fail-empty` now exits correctly
- draft envelopes now report the correct command name:
  - `draft.reply`
  - `draft.reply-all`
  - `draft.forward`
  - `draft.compose`
- `opens --recent` now uses the actual opened `message_id` for metadata
- forward recipient handling was fixed
- timezone detection now strips `posix/` and `right/` prefixes and avoids bad abbreviation fallbacks

### Notes

- This is a **minor** bump because the CLI surface changed materially and gained new user-facing functionality.
- If you have scripts using the old CLI names, update them to the new command forms above.
