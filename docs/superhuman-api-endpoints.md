# Superhuman API endpoints

Last updated: 2026-03-22

## Scope

This document is a reverse-engineered inventory of Superhuman endpoints observed from:

1. our local integration code in `lib/superhuman_comments.py`
2. the current Superhuman web bundle:
   - `https://mail.superhuman.com/~backend/build/7b67e9e565506c551126.page.js`
3. local Superhuman cache files under `~/Library/Application Support/Superhuman/`

It is **not** an official API reference.

For notes on the official Superhuman Mail MCP beta and how it compares to this repo, see:
- `docs/official-superhuman-mcp-beta.md`

## Short answer: can we create drafts inside a Superhuman thread?

**Yes — confirmed for reply, reply-all, forward, and compose drafts.**

The current evidence and live validation suggest:

- there is **no obvious `drafts.create` endpoint**
- Superhuman seems to model drafts as **message objects stored under thread userdata**
- draft persistence appears to happen through:
  - `POST /~backend/v3/userdata.writeMessage`
  - writing to a path like:
    - `threads/<threadId>/messages/<messageId>/draft`
- actual sending is separate and goes through:
  - `POST /~backend/messages/send`

### Evidence

From the current page bundle:

- `writeUserDataMessage(...)` calls:
  - `POST /~backend/v3/userdata.writeMessage`
- `DraftModifiers.saveDraft(...)` persists a write shaped like:
  - `threads/${threadId}/messages/${messageId}/draft`
- new compose flows create a local draft first via:
  - `threads.getNewDraftPresenter(...).initializeDraft(...)`
- team collaboration on drafts uses:
  - `POST /~backend/v3/drafts.share`
  - `POST /~backend/v3/drafts.unshare`
- sending uses:
  - `POST /~backend/messages/send`
  - `POST /~backend/messages/send/cancel`
  - `POST /~backend/messages/send/log`

### Practical conclusion

So the validated model is:

1. choose a thread id
   - for replies / reply-all / forwards: use the existing thread id
   - for new compose drafts: use a brand-new synthetic draft thread id (for example `draft00...`)
2. choose a draft message id
3. persist the draft payload with `userdata.writeMessage`
4. optionally upload attachments and persist attachment metadata separately
5. optionally share/unshare the draft with team endpoints
6. send later with `messages/send`

On 2026-03-22 we successfully validated:

- reply draft creation on an existing thread
- reply-all draft creation on an existing thread
- forward draft creation on an existing thread
- brand-new compose draft creation on a synthetic draft thread id
- attachment upload via `attachments.upload`
- attachment metadata persistence via `threads/<threadId>/messages/<draftId>/attachments/<uuid>`
- `scheduledFor` persistence on drafts
- end-to-end self-send via `POST /~backend/messages/send`
- draft sharing via `POST /~backend/v3/drafts.share`
- draft cleanup by writing `discardedAt`

The core creation path is:

- `threads/<threadId>/messages/<draftId>/draft`

The cleanup path is:

- `threads/<threadId>/messages/<draftId>/discardedAt`

For attachment writes, the validated path is:

- `threads/<threadId>/messages/<draftId>/attachments/<uuid>`

What we have **not** validated yet is the full shape for every advanced draft feature (for example: `drafts.unshare`, smart-send-specific metadata, or every edge case of sending from our own client).

## What we have already validated in this repo

### Auth/bootstrap

Used in `lib/superhuman_comments.py`:

- `GET https://accounts.superhuman.com/~backend/v3/sessions.getCsrfToken`
- `POST https://accounts.superhuman.com/~backend/v3/sessions.getTokens`

### Comments

Used in `lib/superhuman_comments.py`:

- `POST https://mail.superhuman.com/~backend/v3/comments.write`
- `POST https://mail.superhuman.com/~backend/v3/comments.discard`
- `POST https://mail.superhuman.com/~backend/v3/userdata.read`
  - used to read comment containers off a thread

These are live-tested by our comments integration.

### Draft variants

Used in `lib/superhuman_drafts.py`:

- `POST https://mail.superhuman.com/~backend/v3/userdata.writeMessage`
  - to create reply drafts on existing threads
  - to create reply-all drafts on existing threads
  - to create forward drafts on existing threads
  - to create brand-new compose drafts on synthetic draft thread ids
- `POST https://mail.superhuman.com/~backend/v3/userdata.read`
  - to verify the draft exists on the thread
- `POST https://mail.superhuman.com/~backend/v3/attachments.upload`
  - to upload attachment bytes for a draft
- `POST https://mail.superhuman.com/~backend/v3/userdata.writeMessage`
  - to persist uploaded attachment metadata at `threads/<threadId>/messages/<draftId>/attachments/<uuid>`
- `POST https://mail.superhuman.com/~backend/v3/userdata.writeMessage`
  - to discard the draft via `threads/<threadId>/messages/<draftId>/discardedAt`

These are now live-tested by our draft integration.

### Validated draft shapes

#### Reply / reply-all

- `threadId` = existing thread id
- `action` = `reply` or `reply-all`
- persisted at `threads/<threadId>/messages/<draftId>/draft`

#### Forward

- `threadId` = existing thread id
- `action` = `forward`
- minimal payload with empty `to` / `cc` / `bcc` is accepted
- subject `Fwd: ...` is accepted
- `inReplyTo`, `inReplyToRfc822Id`, and `references` are accepted on the forward draft object

#### Compose / new thread

- `threadId` = synthetic draft thread id such as `draft00...`
- `draft.id` = separate synthetic draft message id such as `draft00...`
- first write succeeds with no prior thread history id
- persisted at `threads/<draftThreadId>/messages/<draftMessageId>/draft`

#### Attachments

- upload bytes first via `attachments.upload`
- then write attachment metadata to:
  - `threads/<threadId>/messages/<draftId>/attachments/<uuid>`
- validated metadata fields include:
  - `uuid`, `name`, `type`, `messageId`, `threadId`, `inline`, `source`, `createdAt`, `size`
- validated `source.type` for uploaded files:
  - `upload-firebase`

#### Scheduling

- draft field `scheduledFor` persists successfully through `userdata.writeMessage`

### Sending drafts

Used in `lib/superhuman_send.py`:

- `POST https://mail.superhuman.com/~backend/messages/send`
  - to send a previously persisted draft end-to-end

Validated request envelope:

```json
{
  "version": 3,
  "outgoing_message": {
    "headers": [
      {"name": "X-Mailer", "value": "Superhuman Web (...)"},
      {"name": "X-Superhuman-ID", "value": "<generated>"},
      {"name": "X-Superhuman-Draft-ID", "value": "<draftId>"}
    ],
    "superhuman_id": "<generated>",
    "rfc822_id": "<draft rfc822 id>",
    "thread_id": "<threadId>",
    "message_id": "<draftId>",
    "from": {"email": "...", "name": "..."},
    "to": [{"email": "...", "name": "..."}],
    "subject": "...",
    "html_body": "<div>...</div>",
    "attachments": []
  },
  "delay": 20,
  "is_multi_recipient": true
}
```

Notes:

- when `thread_id` is a synthetic compose thread such as `draft00...`, Superhuman also sends header `X-Superhuman-Thread-ID`
- `In-Reply-To` and `References` are added when the draft carries reply metadata
- self-send was validated live

### Sharing drafts

Used in `lib/superhuman_send.py`:

- `POST https://mail.superhuman.com/~backend/v3/drafts.share`
  - to share an existing draft

Validated request body:

```json
{
  "path": "users/<googleId>/threads/<threadId>/messages/<draftId>",
  "name": "Your Name",
  "add": []
}
```

Validated response shape:

```json
{
  "link": "https://links.superhuman.com/...",
  "containerId": "cont_..."
}
```

Validated readback behavior:

- after sharing, the message-level userdata contains a top-level `sharing` object
- observed fields included:
  - `by`
  - `sharedAt`
  - `accessRole`
  - `name`

## Endpoint inventory observed in the current Superhuman bundle

Observed in `7b67e9e565506c551126.page.js`.

### Messages / sending

- `~backend/messages/send`
- `~backend/messages/send/cancel`
- `~backend/messages/send/log`

### Userdata / threads / drafts / comments

- `~backend/v3/comments.discard`
- `~backend/v3/comments.write`
- `~backend/v3/drafts.share`
- `~backend/v3/drafts.unshare`
- `~backend/v3/messages.modifyLabels`
- `~backend/v3/userdata.amendAgentSessionEvent`
- `~backend/v3/userdata.getThreads`
- `~backend/v3/userdata.promoteAgentSession`
- `~backend/v3/userdata.read`
- `~backend/v3/userdata.searchHistory`
- `~backend/v3/userdata.share`
- `~backend/v3/userdata.sync`
- `~backend/v3/userdata.unshare`
- `~backend/v3/userdata.write`
- `~backend/v3/userdata.writeAgentSession`
- `~backend/v3/userdata.writeMessage`

### AI

- `~backend/v3/ai.analyzeWritingStyle`
- `~backend/v3/ai.askAIProxy`
- `~backend/v3/ai.calendarDetails`
- `~backend/v3/ai.compose`
- `~backend/v3/ai.composeAgentic`
- `~backend/v3/ai.composeEdit`
- `~backend/v3/ai.disable`
- `~backend/v3/ai.enable`
- `~backend/v3/ai.enableTLDR`
- `~backend/v3/ai.populateOccupation`
- `~backend/v3/ai.reprocessTLDR`
- `~backend/v3/ai.semanticSearchProxy.suggestions`
- `~backend/v3/ai.semanticSearchProxy.user`
- `~backend/v3/ai.tldrSync`

### Attachments / containers / links

- `~backend/v3/attachments.upload`
- `~backend/v3/containers.share`
- `~backend/v3/containers.unshare`
- `~backend/v3/links.comment`
- `~backend/v3/links.commentToken`
- `~backend/v3/links.content`
- `~backend/v3/links.open`

### Labels / archive / inbox shaping

- `~backend/v3/autoArchive.deleteRules`
- `~backend/v3/autoArchive.upsertRules`
- `~backend/v3/autoLabels.delete`
- `~backend/v3/autoLabels.modifyConfig`
- `~backend/v3/autoLabels.preview`
- `~backend/v3/autoLabels.upsert`
- `~backend/v3/importanceOverride.create`
- `~backend/v3/importanceOverride.delete`
- `~backend/v3/labels.recentChanges`
- `~backend/v3/labels.resync`
- `~backend/v3/relabels.create`
- `~backend/v3/relabels.revert`
- `~backend/v3/splitInboxes.modify`
- `~backend/v3/syntheticInbox.create`
- `~backend/v3/syntheticInbox.reset`

### Scheduling / smart send / calendar / meetings

- `~backend/v3/autoDrafts.previewEAScheduling`
- `~backend/v3/calendarAvailability.create`
- `~backend/v3/calendarEvents.create`
- `~backend/v3/calendars.authenticate`
- `~backend/v3/microsoftCalendar.proxy`
- `~backend/v3/outOfOfficeResponder.updateForProvider`
- `~backend/v3/smartsend.getTimeRange`
- `~backend/v3/zoom.authenticate`
- `~backend/v3/zoom.createMeeting`
- `~backend/v3/zoom.deleteMeeting`
- `~backend/v3/zoom.signOut`
- `~backend/v3/zoom.updateMeeting`

### Browser / auth / sessions

- `~backend/v3/browsers.create`
- `~backend/v3/browsers.delete`
- `~backend/v3/csrfToken.create`
- `~backend/v3/oauth2.authenticate`
- `~backend/v3/oauth2.signOut`
- `~backend/v3/sessions.getCsrfToken`
- `~backend/v3/sessions.getTokens`
- `~backend/v3/sessions.signIn`
- `~backend/v3/sessions.signInWithAuthCode`
- `~backend/v3/sessions.signOut`

### Teams / members / billing

- `~backend/v3/members.approve`
- `~backend/v3/members.reject`
- `~backend/v3/members.requestUpgrade`
- `~backend/v3/teams.billing`
- `~backend/v3/teams.canInvite`
- `~backend/v3/teams.classify`
- `~backend/v3/teams.getBillingFeaturesBySku`
- `~backend/v3/teams.invite`
- `~backend/v3/teams.join`
- `~backend/v3/teams.members`
- `~backend/v3/teams.resetInviteLink`
- `~backend/v3/teams.suggest`
- `~backend/v3/teams.uninvite`
- `~backend/v3/teams.updateBillingTier`

### CRM / sales

- `~backend/v3/crm.fields/hubspot`
- `~backend/v3/crm.fields/pipedrive`
- `~backend/v3/crm/hubspot`
- `~backend/v3/crm/hubspot/automation/sequences`
- `~backend/v3/crm/hubspot/automation/sequences.enroll`
- `~backend/v3/crm/hubspot/company/search`
- `~backend/v3/crm/hubspot/contact`
- `~backend/v3/crm/hubspot/deal/search`
- `~backend/v3/salesforce.createContact`
- `~backend/v3/salesforce.dereferences`
- `~backend/v3/salesforce.fields`
- `~backend/v3/salesforce.suggestions`
- `~backend/v3/salesforce.updateProfile`

### Knowledge base / blocks / misc

- `~backend/v3/blocks.create`
- `~backend/v3/blocks.delete`
- `~backend/v3/knowledgeBase.delete`
- `~backend/v3/knowledgeBase.upsert`
- `~backend/v3/mcp.generateToken`
- `~backend/v3/metrics.write`
- `~backend/v3/translate.detectLanguage`
- `~backend/v3/unsubscribe`
- `~backend/v3/users.achievements`
- `~backend/v3/users.active`
- `~backend/v3/users.getReferral`
- `~backend/v3/users.refreshAliases`

## Draft-related endpoints: what they seem to do

### Likely core draft persistence

- `~backend/v3/userdata.writeMessage`
  - strongest candidate for creating/updating a draft record on a thread message
- `~backend/v3/userdata.read`
  - read thread/message state back
- `~backend/v3/userdata.getThreads`
  - list/fetch thread-level data
- `~backend/v3/userdata.sync`
  - sync/update local state

### Collaboration on drafts

- `~backend/v3/drafts.share`
- `~backend/v3/drafts.unshare`

These are about sharing an existing draft, not creating one from scratch.
`drafts.share` is now live-validated; `drafts.unshare` is still unvalidated.

### Sending a draft

- `~backend/messages/send`
- `~backend/messages/send/cancel`
- `~backend/messages/send/log`

`messages/send` is now live-validated with a self-send.

## Confidence levels

### High confidence

- comments can be written and discarded
- thread userdata can be read
- sending happens through `messages/send`
- `messages/send` works end-to-end from our own client for a self-send
- reply, reply-all, and forward draft persistence use `userdata.writeMessage`
- new-thread compose drafts can be created by writing to a synthetic draft thread id
- draft attachment bytes can be uploaded through `attachments.upload`
- uploaded attachment metadata can be persisted through `userdata.writeMessage`
- `scheduledFor` persists on drafts
- `drafts.share` shares an existing draft and returns a share link + container id
- shared-draft state is visible in message-level userdata via a top-level `sharing` object
- draft cleanup works by writing `discardedAt` through `userdata.writeMessage`
- `drafts.share` / `drafts.unshare` are not draft creation endpoints

### Medium confidence

- the payloads documented in `lib/superhuman_drafts.py` are sufficient for the validated draft variants above
- the payload documented in `lib/superhuman_send.py` is sufficient for basic send flows
- uploaded attachment metadata with `source.type = upload-firebase` is sufficient for persistence on a draft

### Low confidence / not yet validated

- exact minimal payload for every draft variant
- canonical forward quoted-content generation from live rendered message HTML
- `drafts.unshare` semantics in live team workflows
- smart-send-specific fields beyond plain `scheduledFor`
- advanced send cases: reminders, mail merge, sensitivity labels, smart send internals
- whether there is a hidden create-only endpoint not present in the current bundle

## Suggested next step

If we want to extend support further, the next clean experiment is:

1. validate `drafts.unshare` on the currently shared sacrificial draft
2. capture a real UI-created draft object via `userdata.read`
3. diff it before/after editing the same draft manually in Superhuman
4. compare the UI-created forward draft against our minimal forward payload
5. convert these validations into replayable integration tests

That would move us from “validated draft persistence primitives” to “full client behavior parity”.
