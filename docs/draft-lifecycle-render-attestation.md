# Draft lifecycle and render attestation

**Status:** historical RCA/design record; exact approval/execution details are superseded by [`approval-receipt-issuer-contract.md`](approval-receipt-issuer-contract.md) and [`../authority/README.md`](../authority/README.md)
**Date:** 2026-07-10  
**Scope:** `superhuman-mail` lifecycle reads, sends, and exact-render approval  
**Safety of this investigation:** read-only against live mail after the incident; no drafts, comments, schedules, or sends were created by the RCA

## Executive summary

Two independent problems were exposed by one commercial follow-up:

1. **A draft timestamp was treated as outbound evidence.** Superhuman stores the source draft after a send and attaches a `sendJob` to the message-level wrapper. The nested draft still has a `draft…` ID and a `DRAFT` label. `shm draft read` currently drops the wrapper and its `sendJob`, so even a source draft that has already sent is returned as an active-looking draft. `shm send --dry-run` also ignores `sendJob`, and will currently report that such a terminal draft is sendable again.
2. **The visual check was not the send renderer.** The incident preview put the server-stored body into a custom local HTML page. That proved that the input HTML was structurally valid, but not that Superhuman's editor or outgoing-message pipeline would produce the same DOM. The Superhuman app sanitizes and normalizes editor HTML, then separately adds linkification, tracking, signature, quoted content, and attachment transformations when building the outgoing payload. `shm` currently bypasses that pipeline and sends the raw stored body.

The durable fix is not another timestamp heuristic or a prettier local preview. It is:

- one typed lifecycle classifier with explicit provenance and confidence;
- a terminal send predicate based on a server send acknowledgment plus an immutable provider message identity;
- idempotent send attempts that reconcile unknown outcomes instead of generating a new identity;
- an attestation generated from the **selected live Superhuman draft and Superhuman's own editor/outgoing renderer**;
- an approval fingerprint over every send-affecting field;
- a send-time re-read and exact stale check after the grace period;
- truthful output that distinguishes `requested`, `scheduled`, `backend_confirmed`, and `provider_confirmed` from `sent`.

Until these controls ship, a draft ID, draft label, `date`, `clientCreatedAt`, `sendAt`, HTTP 200, or the current `sent: true` CLI field must not be used alone as evidence that mail left the account.

## Incident timeline

Customer content, addresses, exact times, and live identifiers are deliberately omitted. The timeline uses fully synthetic identifiers because this repository is public.

| Relative time | Evidence | Interpretation |
|---|---|---|
| T−21d | Earlier source draft `draft_fixture_earlier` created | Draft creation, not a send |
| T−21d +2m | Provider message `message_fixture_earlier`; terminal `sendJob.sentAt` | Earlier follow-up was sent |
| T0 | Source draft `draft_fixture_incident` receives new `clientCreatedAt` and `date` values | This was the activity later mistaken for a send |
| T0 +4m | A workflow calls the new draft activity a fresh send | False positive: the latest real sent message was still the earlier follow-up |
| T0 +6m | Superhuman draft evidence and an independent provider sent search are checked | Correction: active draft, no fresh send |
| T0 +~3h | The account owner explicitly approves this one draft after a visual/thread check | Approval to send, not evidence of completion |
| T0 +3h07m | Immutable provider message `message_fixture_incident` appears with `SENT`, matching source-draft and Superhuman IDs | Provider-confirmed send |
| T0 +3h07m05s | Source wrapper gains `sendJob.sentAt` and `sendVerifiedAt` | Backend-confirmed terminal send |

The post-incident read is especially important:

- the source draft still has `labelIds: ["DRAFT"]`;
- the wrapper now has `sendJob.messageId: message_fixture_incident` and terminal timestamps;
- the immutable provider message has `labelIds` containing `SENT`, `superhumanOwnDraftId: draft_fixture_incident`, and the same `superhumanId` as the job;
- **current `shm draft read` still returns that source draft as one draft and omits its send job**;
- **current `shm send --dry-run` still returns `sendable: true` for it**.

That last pair safely reproduces the lifecycle bug without sending anything.

## Evidence and confirmed root causes

### 1. The storage model has two identities, not one

A Superhuman source draft lives under:

```text
threads/<thread_id>/messages/<draft_id>/draft
```

A completed send adds message-level state alongside the draft:

```text
threads/<thread_id>/messages/<draft_id>/sendJob
```

The immutable provider message has a different non-draft message ID. It links back through `superhumanOwnDraftId` / `superhumanDraftId` and `superhumanId`.

Therefore:

- `draft.id` identifies editable/source state;
- `sendJob.messageId` identifies the provider message produced from it;
- those values are not interchangeable;
- a nested draft can remain present after its send is terminal.

This is visible in the live redacted incident and in Superhuman's current client model. For the owner's ordinary draft, the app treats pending, terminal, or failed send-job states as a read-only "sent draft" class; unless the job is scheduled for the future, its constructor overlays `SENT` and later methods distinguish sending from failure. That overlay can therefore appear while an optimistic/undo-window job is still pending—or on a failed job—while the raw nested draft still says `DRAFT`. Presentation labels alone are not canonical completion evidence in either direction.

### 2. `draft.read()` discards lifecycle information

`superhuman_mail/draft.py::read()` iterates message wrappers but appends only `msg_data["draft"]`. It filters `discardedAt`, but does not return or classify:

- `sendJob`;
- `sending`;
- `historyId`;
- terminal provider message identity;
- thread replacement for synthetic compose threads.

The command name and `draft_count` then make a historical sent source look active.

### 3. `send.validate()` allows a terminal source draft

`superhuman_mail/send.py::validate()` checks only:

- draft presence;
- `discardedAt`;
- recipients, subject, body, and attachments.

It never checks `sendJob`. A completed source draft is therefore validated as sendable again. This is a direct duplicate-send risk, not only a reporting defect.

### 4. `send.execute()` reports acceptance as completion

`send.execute()` posts to `/~backend/messages/send` with a default 20-second delay and immediately returns:

```json
{"sent": true}
```

The Superhuman client itself first creates an optimistic send job (`notSentToServer`, `superhumanId`, and `sendAt`) and then submits the request. Completion arrives later through `sentAt`, `sendVerifiedAt`, and `messageId`. The 20-second backend delay is also the undo-send window.

An HTTP success means the job was accepted. It does not prove that the undo window elapsed, that the backend persisted the send, or that a provider message exists.

### 5. Retries are not idempotent in `shm`

`_build_outgoing()` generates a fresh `superhuman_id` on each call. If an HTTP response is lost or a caller retries after an unknown result, the retry can carry a new identity. The current code has no attempt journal, no per-draft lock, and no reconciliation before retrying.

The Superhuman app handles retries around one outgoing message identity. `shm` does not preserve that property.

### 6. Execute bypasses an already-permissive validation path

`send.execute()` does not call `send.validate()`. In addition to ignoring send jobs, it does not reject a message-level `discardedAt` or rerun recipient/body checks before POST.

The existing validator is also permissive: missing subject/body become warnings, and `sendable` becomes true whenever `to` is nonempty. It does not prove that parsed addresses are valid. An authoritative preflight must run inside execute; empty subject may be allowed only when it was explicit in the attested approval, while discarded drafts, invalid/empty recipients, and empty bodies must fail.

### 7. The normalized local message view omits proof fields

`superhuman_mail/_local.py::get_messages()` intentionally returns a concise view. It omits:

- `labelIds`;
- `superhumanId`;
- `superhumanOwnDraftId` / `superhumanDraftId`;
- `rfc822Id` and reply linkage.

The raw local thread contains those fields, but the purpose-built CLI output does not. An agent must currently use advanced/raw data or a second provider to prove a send.

### 8. The preview and send pipelines diverge

The incident preview was a custom HTML page with its own Arial CSS. It included the exact raw body hash, which was useful input evidence, but it did not run Superhuman's rendering path.

Observed Superhuman desktop/web build behavior (app `1041.0.15`, web code version `2026-07-09T19:06:39Z`):

1. The compose controller passes `draft.getBody()` to the Squire editor.
2. Squire's `setHTML()` sanitizes with DOMPurify, then runs tree cleanup, `<br>` cleanup, container fixing, and cursor-block normalization.
3. Paste uses a separate MailJanitor path. It converts Microsoft lists, removes hidden content and selected attributes/styles, rewrites links, and normalizes images.
4. On submit, the compose controller reads the editor's normalized `getHTMLSafe()`, updates the in-memory draft, and builds the send action from that value.
5. `OutgoingMessage.fromDraft()` invokes `BodyContent.generateForOutgoingMessage()`.
6. That outgoing path reparses HTML and performs linkification, inline-image/CID handling, read-pixel cleanup/insertion, signature handling, Carbon/autocorrection cleanup, quoted-content assembly, and outer wrapping.
7. The resulting `html_body`, not merely the originally stored body, is sent.

By contrast, `superhuman_mail/send.py::_build_outgoing()` uses:

```python
"html_body": draft.get("htmlBody") or draft.get("body") or ""
```

It does not run the editor or outgoing renderer.

### 9. A body-only hash is insufficient

The incident hash covered only `draft.body`. It did not bind:

- account/from alias;
- to, cc, or bcc;
- subject;
- thread and reply anchor;
- references / RFC822 IDs;
- attachments and inline CIDs;
- `quotedContent` and `quotedContentInlined`;
- scheduling, reminder, abort-on-reply, or sensitivity fields;
- signature settings or generated signature HTML;
- Superhuman app/code version;
- editor-normalized or outgoing HTML.

A recipient, thread anchor, attachment, or signature could change while the approved body hash remained identical.

## What was not proven

The distinction matters:

- The incident draft and an untouched comparison draft both rendered cleanly in the live Superhuman editor during this RCA.
- No historical malformed outgoing MIME was available, so the exact markup that caused prior line-break problems is unknown.
- A recipient-client rendering defect (for example, Outlook-specific behavior) was not reproduced.
- The current local provider cache often exposes text but not the sent HTML part, so it cannot attest the full MIME body by itself.

The confirmed rendering root cause is **path divergence and lack of binding**, not a claim that one particular HTML tag always breaks. A controlled test mailbox is required before asserting recipient-client equivalence.

## Lifecycle model

### Invariants

1. A value whose ID starts with `draft` is never outbound evidence.
2. `draft.date`, `clientCreatedAt`, wrapper `historyId`, and cache recency are never send timestamps.
3. `sendJob.superhumanId`, `sendAt`, `scheduledFor`, `sending`, `notSentToServer`, and HTTP 2xx are non-terminal.
4. A raw `DRAFT` label describes the nested source object; it does not override terminal send evidence on the wrapper/provider message.
5. A business workflow may say **sent** only when it has an immutable provider message identity linked to the draft, with a provider sent label/date or an equivalent provider-authoritative acknowledgment.
6. Every lifecycle result includes provenance, observation time, and confidence.
7. Lifecycle is classified per `(account, thread_id, draft_id)`, never only per thread.
8. A terminal draft cannot be sent again. A deliberate second email requires a new draft.
9. Unknown send outcomes are reconciled before retry. Retries reuse the same attempt and `superhuman_id`.
10. Follow-up tasks are created only from provider-confirmed sends.

### Typed states

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

Classification first evaluates contradictory terminal combinations, then determines the material outcome:

1. If a valid immutable provider `SENT` message exists, the material outcome is `sent_provider_confirmed` and `outbound_evidence: true`, even if stale failure/abort metadata also exists; set `consistency: conflicting` and alert.
2. Without provider proof, contradictory success/failure/abort fields are `inconsistent` with `outbound_evidence: false`.
3. Otherwise, backend terminal `sentAt` + `messageId` is `sent_backend_confirmed`.
4. Then classify failure / abort, pending or scheduled job, discarded, and active draft in that order.

Outcome and consistency are separate axes. Contradictory metadata must never be hidden by precedence, but an immutable provider message must also never be treated as unsent or retried.

Provider confirmation requires one of two unambiguous proof sets; subject, timestamp proximity, sender name, or mere presence in the same thread never qualifies:

```text
A. Normal completed job
   provider.id == sendJob.messageId
   provider has SENT and is from the bound account
   every present provider draft/Superhuman linkage field matches the wrapper

B. Stale userdata / missing job messageId
   provider.superhumanOwnDraftId == draft.id
   provider.superhumanId == the persisted send-job or attempt superhumanId
   provider has SENT and is from the bound account
```

If neither proof set is complete, the provider observation remains unconfirmed/inconsistent even if a nearby message is `SENT`. For a synthetic compose thread, follow `threadReplacement.movedToThreadId` and/or `sendJob.threadId` before looking up the provider message.

A stored `draft.scheduledFor` with no `sendJob` is only intended scheduling metadata; it remains an `active_draft` that may be submitted to create the scheduled job. `scheduled` requires an existing nonterminal send job with a future `sendAt`.

`sendVerifiedAt` strengthens backend confirmation but should not be mandatory: Superhuman's own model uses `sentAt` as the terminal field, and older/provider-specific data may omit `sendVerifiedAt`.

### Confidence and provenance

```json
{
  "account": {
    "provider_user_id": "…",
    "email_hash": "sha256:…"
  },
  "thread_id": "…",
  "draft_id": "draft…",
  "state": "sent_provider_confirmed",
  "terminal": true,
  "outbound_evidence": true,
  "confidence": "provider_confirmed",
  "timestamps": {
    "draft_created_at": "…",
    "scheduled_for": null,
    "send_at": "…",
    "sent_at": "…",
    "send_verified_at": "…",
    "provider_message_at": "…"
  },
  "provider_message": {
    "id": "…",
    "labels": ["SENT"],
    "superhuman_own_draft_id": "draft…"
  },
  "observations": [
    {
      "source": "superhuman_userdata_api",
      "history_id": 0,
      "observed_at": "…"
    },
    {
      "source": "superhuman_local_cache",
      "observed_at": "…"
    }
  ],
  "consistency": "matched"
}
```

If userdata is terminal but the local provider cache has not caught up, report `sent_backend_confirmed`, `outbound_evidence: false` for commercial automation, and continue polling. If a linked provider `SENT` message exists while userdata is stale, provider evidence wins but the mismatch remains observable.

## Proposed CLI and Python API

### Add lifecycle reads

```bash
shm draft status <thread_id> [--draft-id <id>] --account <email>
shm send status <thread_id> <draft_id> [--wait 120] --account <email>
shm thread timeline <thread_id> --account <email>
```

Strict lifecycle and send operations require an explicit account. Resolve it once to the immutable provider/user ID, use that identity for both userdata and local-cache reads, and persist it in the lifecycle result/attempt. In a single-account compatibility period, omission may use the configured account with a warning; a multi-account setup must fail rather than guess.

`thread timeline` returns typed entries (`provider_message`, `draft`, `send_event`) with separate event times. It never merges a source draft and provider message into one timestamped item.

### Correct `draft read`

In the additive release, preserve `data.drafts` but add:

```json
{
  "active_draft_count": 0,
  "terminal_draft_count": 1,
  "lifecycle_by_draft_id": {
    "draft…": {"state": "sent_provider_confirmed", "…": "…"}
  }
}
```

Emit a warning when terminal source drafts are included. Add `--active-only` and `--include-terminal`; make active-only the default in the next major version.

### Replace ambiguous send output

Deprecate unconditional `sent: true`. Return:

```json
{
  "attempt_id": "…",
  "state": "send_pending_undo",
  "accepted": true,
  "sent": false,
  "provider_confirmed": false,
  "superhuman_id": "…",
  "draft_id": "draft…"
}
```

Only `sent_provider_confirmed` may return `sent: true` in strict mode.

Suggested exit behavior:

- `0`: preview succeeded, scheduled state was explicitly requested, or provider-confirmed send;
- `1`: rejected/failed;
- `4`: accepted but still pending/unknown after the requested wait.

### Add exact render attestation

```bash
shm draft attest-render <thread_id> <draft_id> \
  --account <email> \
  --surface superhuman-desktop \
  --output <directory>

shm send --confirm <thread_id> <draft_id> \
  --account <email> \
  --attestation <attestation_id> \
  --approval-ref <opaque-reference> \
  --wait 120
```

`--dry-run` remains as a metadata preflight, but must say `render_attested: false` and `send_eligible: false` when strict visual proof is required.

## Render attestation design

### Requirement

The attestation targets the exact **server-stored** draft, not unsaved editor state. It must prove that the live model is byte-for-byte bound to that server snapshot before using the actual Superhuman compose/outgoing path. A dirty editor, another pane with unsaved changes, activation-triggered autosave, or a local page that merely embeds raw HTML cannot authorize a strict send.

### Adapter

Build a version-gated renderer adapter that attaches read-only to a dedicated Superhuman desktop/web renderer session. It must:

1. resolve the explicit account to its immutable provider/user ID and read server snapshot A from `userdata.read`;
2. open the exact draft/thread without focusing or editing the body;
3. obtain the live `DraftModel.id`, `threadId`, dirty state, and complete send-affecting model JSON—not subject text;
4. require the live model's source fields to equal server snapshot A and require no dirty/unsaved editor state;
5. re-read server snapshot B; if activation/autosave changed history or any send-affecting value, abort rather than blessing the new state;
6. capture the editor's normalized DOM and a compose screenshot;
7. invoke the in-renderer equivalent of `draft.asMessage(...).getDraftHtmlBody(...)`, which calls Superhuman's `OutgoingMessage.fromDraft()` / `BodyContent.generateForOutgoingMessage()` with live account settings;
8. capture the exact `OutgoingMessage.toJsonRequest()` payload without calling `/messages/send`;
9. render that outgoing HTML through Superhuman's own message renderer for a second screenshot;
10. re-read server snapshot C and prove A = B = C for the chosen source fields, and prove that no send, cancel, postpone, draft-write, or comment-write endpoint was requested.

A practical first adapter can use CDP and the React component's live model references. Because that is private and version-sensitive, it must be allowlisted by Superhuman code version and fail closed on any missing field. The long-term preferred solution is an official Superhuman preview endpoint/tool.

### Approval fingerprint

Canonicalize and hash all send-affecting values:

```text
account identity and from alias
thread_id, draft_id, action
inReplyTo, inReplyToRfc822Id, references, rfc822Id
ordered to/cc/bcc
subject
raw stored body bytes
editor-normalized HTML
exact outgoing request JSON / html_body
quotedContent + quotedContentInlined
attachment metadata, source identity, inline CID, size, and a stable provider digest or verified byte hash
scheduledFor, abortOnReply, reminder, sensitivity fields
signature/settings hash
Superhuman app and web code versions
server history IDs
```

Use both:

- exact SHA-256 values for stale checks;
- canonical structural and semantic-text hashes for human-readable diffs.

Observation timestamps are signed provenance outside the equality-bound send fingerprint. Record attestation-time and send-time observations separately; they are expected to differ and are never compared as content.

Strict attestation fails with `UNATTESTABLE_ATTACHMENT` unless every attachment has a stable provider digest or readable bytes whose hash can be reverified at send time. Metadata-only attachment approval is never `send_eligible`, because source metadata can stay constant while bytes change.

The attestation is canonical JSON with:

- `attestation_id = sha256(canonical_json)`;
- short expiry;
- HMAC-SHA256 signature using a local Keychain-held key;
- one reserved `superhuman_id` used by both attestation-time and send-time rendering and every retry;
- screenshot hashes and local artifact paths;
- `confidence: exact_superhuman_renderer`;
- `send_eligible: true|false`.

### Stale check

After the external grace countdown and immediately before POST:

1. lock `(account, draft_id)`;
2. resolve the explicit account again and require the same immutable provider/user ID, then re-read server userdata and local/provider state for that account;
3. reject any terminal or scheduled state inconsistent with the requested operation;
4. create/resume the attempt journal using the `superhuman_id` reserved in the attestation;
5. run a second version-gated, no-write Superhuman renderer probe using that same `superhuman_id`;
6. recompute the complete source, editor, signature/settings, app-version, attachment, and exact outgoing-payload fingerprint;
7. reject if rerendering is unavailable, activates an autosave/dirty state, or any exact value differs from the approved attestation;
8. POST the freshly probed payload bytes only after they compare equal to the approved payload;
9. persist the pre-network attempt state before network I/O.

A mismatch returns `STALE_ATTESTATION` with field-level, content-free diffs and requires a fresh preview and approval.

## Idempotent send and reconciliation

### Attempt journal

Use a local SQLite journal in one canonical per-user state directory. It is append-only for audit fields and has a unique active claim on `(immutable_provider_user_id, draft_id)`.

This guarantees one POST only for cooperating `shm` processes sharing that journal. It cannot serialize a different host/state directory, the native Superhuman UI, or an uncooperative caller. Global exactly-once behavior requires a vendor-supported idempotency key or compare-and-set send contract; until then, expose `idempotency_scope: local_cooperating_processes` and treat cross-surface concurrency as a residual risk.

Record before POST:

```text
attempt_id
immutable provider/user ID + account hash
thread_id / draft_id
attestation_id / approval reference
superhuman_id (reserved during attestation and reused for send-time rerender/retries)
outgoing fingerprint
created_at
state
HTTP attempt count and response classification
last reconciliation result
provider message id and terminal timestamps when known
```

Never record body or recipient text in telemetry. Raw account/thread/draft/message IDs exist only in the local reconciliation journal, under a `0700` state directory and `0600` database, never in synced artifacts; purge terminal rows after a documented short retention window (proposed: 30 days). Exported telemetry uses Keychain-keyed HMAC-SHA256 pseudonyms for every mailbox object ID.

### Retry rules

- Reuse one `superhuman_id` for all retries of an attempt.
- On timeout, connection reset, or HTTP 409, reconcile userdata and provider cache before another POST.
- If a matching pending or terminal job exists, do not POST again.
- If the outcome remains unknown, return nonzero pending/unknown; do not mint a new identity automatically.
- A terminal source draft is permanently blocked from another send. Create a new follow-up draft instead.
- Before global rollout, obtain or validate a server idempotency/CAS contract. The local journal is not represented as global exactly-once protection.

### Post-send wait

For immediate sends, poll with bounded backoff until one of:

- linked provider `SENT` message → success;
- terminal backend failure/abort → failure;
- timeout → pending/unknown, exit 4.

For future scheduled sends, return `scheduled` with the intended time and no `sent` claim.

## Failure model

| Failure | Current risk | Durable behavior |
|---|---|---|
| Draft updated after an older sent message | Recency mistaken for send | Per-draft lifecycle; separate event clocks |
| Raw `DRAFT` remains after send | Sent source appears active | Wrapper/provider classifier; active-only default |
| Future scheduled send | `sendAt` mistaken for completion | `scheduled`, `outbound_evidence: false` |
| Discarded draft remains in userdata | Presence mistaken for active | `discarded`, terminal but never outbound |
| Same thread has several drafts | Thread-level answer picks wrong one | Key every result by draft ID |
| Execute bypasses permissive validation | Discarded/empty/invalid draft can reach POST | One authoritative execute-time preflight; explicit empty-subject policy |
| HTTP 2xx during undo window | CLI says sent too early | `accepted/pending`; wait for provider identity |
| Local cache lags backend | False unsent or duplicate retry | Expose mismatch; poll/reconcile |
| Userdata lags provider | False pending | Provider linkage can confirm; retain mismatch |
| Response lost after POST | New ID can duplicate | Journal + reused identity + reconcile-before-retry |
| Two cooperating local agents send concurrently | Duplicate POSTs | Canonical local lock + server re-read + one attempt identity |
| UI/other host sends concurrently | Local lock cannot serialize it | Residual risk until server idempotency/CAS exists; do not claim global exactly-once |
| Body edited after approval | Approved text differs from send | Full fingerprint stale rejection |
| Recipient/thread changed with same body | Wrong recipient/thread can pass body hash | Envelope and thread binding in fingerprint |
| Valid HTML uses different editor semantics | Local preview looks fine | Exact in-app editor/outgoing attestation |
| Script/unsafe markup | Browser/editor strips it later | Record sanitized diff; reject until approved |
| CSS/classes transformed | Compose and transport differ | Capture both editor and outgoing HTML/screenshots |
| `<p>`, `<br>`, or list normalized | Line breaks/bullets drift | Structural hash + actual renderer proof |
| Bare URL/email auto-linkified | Approved bytes differ from transport | Hash exact outgoing payload |
| Signature changes | Unapproved content added | Signature/settings hash in attestation |
| Quoted content appended separately | Body-only preview omits it | Include quote state and outgoing render |
| Plain-text fallback differs | Recipient sees collapsed text | Post-send MIME observation; controlled E2E fixture |
| Superhuman app update | Private adapter silently drifts | Version gate and fail closed |

## Exact test plan

### Unit: lifecycle classifier

Use table-driven fully synthetic incident-shape fixtures for:

1. plain active draft;
2. active draft newer than the last provider sent message;
3. raw `DRAFT` plus terminal `sendJob` plus matching provider `SENT` message;
4. active draft with `scheduledFor` but no send job;
5. send job with future `sendAt` and no `sentAt`;
6. optimistic `notSentToServer` job;
7. pending undo window;
8. `failedAt` without `sentAt`;
9. aborted scheduled send;
10. discarded draft with no job;
11. sent, active, and discarded drafts on one thread;
12. backend terminal while provider cache is stale;
13. provider terminal while userdata is stale;
14. provider `SENT` missing the minimum linkage fields remains unconfirmed;
15. unrelated nearby `SENT` message on the same thread never confirms the draft;
16. synthetic compose thread with `threadReplacement`;
17. backend success plus failure/abort without provider proof → `inconsistent`;
18. provider-confirmed `SENT` plus stale failure/abort metadata → sent outcome with `consistency: conflicting`;
19. duplicated/mismatched provider linkage → `inconsistent`;
20. a draft ID by itself never sets `outbound_evidence`.

### Unit: send safety

1. One authoritative preflight runs inside both validate and execute.
2. Terminal source draft is rejected by validate and execute.
3. Discarded draft is rejected by validate and execute.
4. Empty/invalid recipients and empty body are rejected; empty subject follows an explicit attested policy rather than a warning-only bypass.
5. A draft with an existing scheduled/pending send job cannot be submitted again; `scheduledFor` intent without a job remains submit-able once.
6. HTTP 2xx returns pending, never unconditional sent.
7. Wait transitions pending → backend-confirmed → provider-confirmed.
8. Timeout exits 4 with the same attempt identity.
9. HTTP 409 reconciles rather than creating a new attempt.
10. Network retry reuses `superhuman_id`.
11. Two cooperating processes sharing the canonical journal result in one POST.
12. Unknown outcome cannot be retried with a new identity without resolution.
13. Strict commands fail if the configured/explicit account resolves to a different immutable user ID before POST.
14. Send-time rerender uses the attested reserved `superhuman_id`; any editor/outgoing/signature/version mismatch or unavailable renderer blocks before POST.

### Unit: approval fingerprint

Every one-field mutation must fail the stale check:

- from/to/cc/bcc;
- subject;
- body bytes;
- thread/reply anchor/references;
- quote content/inlining flag;
- attachment/CID/source/bytes;
- unreadable or remote attachment without a stable provider digest (must be unattestable);
- unchanged attachment metadata with changed bytes/digest;
- schedule/reminder/sensitivity;
- signature/settings;
- Superhuman code version.

Also test that a changed recipient with identical body is rejected.

### Contract: private API and local cache

Fully synthetic fixtures should preserve the incident's structural shapes:

- `userdata.read` wrapper with source draft and terminal `sendJob`;
- raw local provider message with `SENT`, `superhumanOwnDraftId`, and matching `superhumanId`;
- local normalized view missing proof fields, to prevent accidental classifier use;
- `/messages/send` accepted response followed by eventual userdata/cache transitions.

### Contract: Superhuman renderer

For every allowlisted app/code version:

- `<p>A</p><p>B</p>` keeps two visible paragraphs;
- `Best regards,<br>Name` keeps the visible break;
- `<ul><li>…</li></ul>` keeps list structure and bullets;
- bare links match exact outgoing linkification;
- unsafe/disallowed markup produces an explicit sanitized diff;
- CSS/class cleanup is reflected in outgoing HTML;
- signature, quoted content, read pixel, and inline CID behavior match the in-app payload;
- captured request JSON equals `OutgoingMessage.toJsonRequest()` byte-for-byte after canonical JSON serialization.

### Integration: fake backend

Run a fake userdata/send/provider service with configurable delay and cache lag. Prove:

- no duplicate on lost response;
- no duplicate under cooperating local concurrent callers;
- accurate states through eventual consistency;
- stale edit during the 60-second gate blocks the send after the mandatory second no-write renderer probe;
- scheduled send never reports sent;
- a terminal source draft cannot be re-sent;
- account/config switching during the grace window blocks;
- cross-host/native-UI concurrency is recorded as unsupported until a server CAS contract exists.

### Real non-sending end-to-end proof

Against a dedicated fixture draft in the actual Superhuman app:

1. record server history, draft fingerprint, and absence of a send job;
2. open exact `(thread_id, draft_id)` without editor input;
3. prove the live model IDs, immutable account ID, non-dirty state, and equality with the server source snapshot;
4. abort if activation/autosave changes history or if another pane has unsaved state; otherwise capture editor DOM and screenshot;
5. generate exact outgoing request/render inside Superhuman;
6. capture outgoing screenshot and hashes;
7. assert network logs contain no `/messages/send`, cancel, postpone, draft write, or comment write;
8. re-read server data and assert history/body/envelope are unchanged;
9. emit a signed, send-ineligible test attestation artifact for review.

This is the minimum real visual proof. A local standalone HTML page does not pass.

### Controlled transport test before rollout

With separate explicit approval and a dedicated test account/mailbox only:

- send one fixture per paragraph/list/link/signature/inline-image case;
- verify provider `SENT` linkage and idempotency;
- retrieve raw MIME from Sent and the receiving mailbox;
- compare `text/html` and `text/plain` structures;
- capture Gmail and Outlook test-client screenshots where support is claimed.

No customer thread is a test fixture.

## Observability and audit

Emit structured, content-free events:

```text
draft.lifecycle_observed
render.attestation_started
render.attestation_completed
render.attestation_failed
send.preflight_blocked
send.attempt_created
send.request_accepted
send.backend_confirmed
send.provider_confirmed
send.reconciliation_timeout
send.retry_reused_identity
send.blocked_terminal
send.blocked_stale_attestation
```

Common fields:

```text
attempt_id, attestation_id
keyed hash of provider/user ID and account identity
keyed hashes of thread, draft, and provider-message IDs where available
idempotency scope (`local_cooperating_processes` until server CAS)
previous/current lifecycle state
confidence and provenance sources
userdata history id and local cache observation time
outgoing fingerprint (never body)
Superhuman app/code version and renderer adapter version
HTTP class, retry count, and state-transition latency
approval reference (opaque; no raw Slack/email content)
```

Track counters and alerts for:

- terminal drafts returned by active draft reads;
- terminal drafts that validate as sendable (must become zero);
- stale-attestation blocks;
- reconciliation timeouts;
- provider/backend inconsistencies;
- duplicate provider messages for one draft/attempt;
- renderer version mismatches.

## Ownership by repository/system

### `superhuman-mail`

Owns:

- lifecycle classifier and provenance schema;
- purpose-built status/timeline reads;
- terminal/scheduled/pending send guards;
- truthful send response semantics;
- idempotent attempt journal, lock, retry, and reconciliation;
- full approval fingerprint and stale check;
- exact Superhuman renderer adapter and attestation artifact;
- fixtures, contract tests, and non-sending visual E2E.

### `pi-setup` / `send-gate`

Owns only generic outbound orchestration:

- recognize provider preview/status contracts instead of printing arguments only;
- show attestation ID, state, recipient/thread summary, and expiry;
- run the grace period;
- stop printing unconditional `Sent successfully` for an accepted/pending command;
- surface the child's final typed state.

It must not reimplement Superhuman lifecycle or hashing. The send-time stale check belongs inside `shm`, after the countdown.

### Commercial agent policy / automation

Owns:

- requiring exact attestation for customer mail;
- carrying the human approval reference into the send;
- never treating draft recency as outbound evidence;
- waiting for provider-confirmed state before CRM activity or follow-up task creation;
- optional independent Gmail/Superhuman confirmation for high-risk customer sends;
- redacted audit reporting.

### Superhuman dependency

Open a vendor request for a supported, read-only "render exact outgoing draft" operation and a typed send-status/idempotency compare-and-set contract. The private renderer adapter remains version-sensitive until that exists, and global exactly-once behavior remains unclaimed.

No implementation issue was created during this read-only RCA. At least three tracked implementation workstreams are required: `superhuman-mail`, `send-gate`, and commercial policy/automation.

## Compatibility and migration

1. **v0.x additive release**
   - add classifier/status/timeline;
   - add lifecycle data and warnings to existing reads;
   - change `sent` to false unless terminal, while retaining the field;
   - add terminal duplicate-send block;
   - add opt-in strict attestation.
2. **Rollout flag**
   - `SHM_SEND_SAFETY=v2` for selected accounts;
   - compare legacy and new classification in read-only shadow mode;
   - no shadow sends.
3. **Commercial enforcement**
   - require strict attestation and provider confirmation for customer mail;
   - update send-gate wording/preview.
4. **Next major**
   - active-only `draft read` default;
   - require attestation for irreversible sends;
   - remove ambiguous direct `Client.send.execute()` behavior.

Python callers get additive lifecycle methods first. A deprecation warning should direct callers from `Client.send.execute()` to `prepare()` / `execute(attestation_id=…)` before the signature becomes strict.

## Phased implementation slices

### Slice 0 — immediate policy guard

- Document the canonical evidence rules.
- Never use `draft.read` alone as sent/unsent proof.
- Re-read raw wrapper plus provider message before any current `--confirm`.
- Treat local HTML previews as approximate.

### Slice 1 — lifecycle correctness

- Add `lifecycle.py`, fully synthetic fixtures, status/timeline commands.
- Fix active draft counts.
- Block terminal/pending/scheduled resends.
- Replace unconditional `sent: true`.

This slice removes the duplicate-send hazard even before visual attestation exists.

### Slice 2 — attempts and eventual consistency

- Add journal/locking.
- Reuse `superhuman_id`.
- Add wait/reconciliation and exit 4.
- Add concurrency and unknown-outcome integration tests.

### Slice 3 — exact renderer attestation

- Build version-gated CDP adapter.
- Capture live model, editor DOM, exact outgoing JSON, and two screenshots.
- Sign attestation and implement full stale check.
- Complete real non-sending E2E.

### Slice 4 — orchestration rollout

- Integrate send-gate typed preview/results.
- Require approval reference in commercial workflows.
- Run controlled transport fixtures.
- Roll out account by account.

## Rollback and fail-safe behavior

- Lifecycle reads are additive and can remain even if strict sending is rolled back.
- Journal and renderer are feature-flagged independently.
- A renderer adapter/version failure blocks strict send; it never silently falls back to raw HTML.
- A temporary emergency bypass, if retained at all, is limited to dedicated test accounts and must not be enabled for customer mail.
- Local journal schema migrations are reversible by backup/recreate; no remote mail migration is required.
- Send-gate can revert independently because `shm` remains the authoritative stale/lifecycle guard.
- New Superhuman app versions start unsupported until the renderer contract suite and non-sending E2E pass.

## Acceptance criteria

The design is complete only when all are true:

1. `draft read` does not present a sent source draft as active without an explicit terminal lifecycle.
2. The fully synthetic incident-shape fixture classifies new draft activity as a draft before approval and as one provider-confirmed send only after the immutable message appears.
3. One authoritative execute-time preflight rejects terminal, discarded, empty-body, and invalid-recipient drafts; empty subject follows an explicit attested policy.
4. `send --dry-run` rejects the terminal incident-shape source draft; `--confirm` cannot post it again.
5. HTTP success during the undo window never yields `sent: true`.
6. Every retry reuses one attempt/`superhuman_id`; cooperating local callers sharing the canonical journal make one POST. Global exactly-once is gated on a server idempotency/CAS contract and is not claimed before then.
7. A stale change to any send-affecting field after approval blocks at send time; any attachment lacking re-verifiable bytes or a stable digest is not send-eligible.
8. Strict preview proves immutable account, live draft, and thread IDs; proves live-model equality with unchanged server state; captures the actual Superhuman editor and exact outgoing payload/render; and makes no write/send request.
9. After the grace period, a second no-write renderer probe with the reserved `superhuman_id` recomputes the full exact fingerprint; unavailable or changed output blocks, and only freshly probed bytes equal to the approved payload are posted.
10. The attestation is signed, versioned, expiring, and content-bound.
11. Immediate sends return success only after provider-confirmed immutable message identity; scheduled sends remain `scheduled`.
12. Follow-up automation consumes provider-confirmed send events only.
13. Send-gate no longer claims success for pending/unknown state.
14. Unit, contract, fake-backend integration, renderer contract, and real non-sending visual E2E suites pass on an allowlisted Superhuman build.
15. A controlled test-mailbox transport suite passes before customer rollout.
16. Exported logs contain keyed identifier hashes/state/provenance but no raw mailbox IDs, body, recipient content, credentials, or private approval text; raw reconciliation IDs remain only in the permission-restricted, short-retention local journal.
