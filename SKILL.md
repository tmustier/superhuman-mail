---
name: superhuman-mail
description: >
  Interact with Superhuman email via the `shm` CLI — search threads, read messages,
  inspect opens/read receipts, create drafts (reply, reply-all, forward, compose),
  post/read/discard comments, upload attachments, share/unshare drafts, and send email.
  Use when the user asks to work inside Superhuman rather than Gmail. Do NOT use when
  the user explicitly wants `gog gmail` or a Gmail-native workflow.
---

# Superhuman Mail

Use the `shm` CLI to work with Superhuman's private API and local desktop cache.

This is an unofficial, reverse-engineered integration — not an official Superhuman SDK.

## Prerequisites

`shm` requires:
1. Superhuman desktop app installed and signed in
2. `uv` and Python 3.11+
3. Node.js 22+ for exact live-render probing
4. The repository's `scripts/setup.sh` has installed the self-contained `shm` launcher on PATH

## Setup

Install the launcher once from the package checkout, then bootstrap directly from the local Superhuman app:

```bash
./scripts/setup.sh
shm setup
shm doctor
```

The launcher provisions its `cryptography` dependency through `uv`; an activated virtualenv is not required.

If multiple Superhuman accounts are signed in, pick one explicitly:

```bash
shm setup --email someone@example.com
```

If the config lives elsewhere:

```bash
shm setup --config /path/to/config.json
export SUPERHUMAN_MAIL_CONFIG=/path/to/config.json
```

If `shm doctor` fails, make sure Superhuman is running and signed in, then rerun `shm setup` (with `--email` if you have multiple accounts).

## Core safety rule

**For general outbound, never send without explicit user approval and an exact live-render attestation.**

The sole unattended exception is the designated qualified website-inbounds automation. It may call `shm send --qualified-website-inbound` only after its own person/company qualification, timezone, calendar, draft-quality, exact-recipient, and dry-run gates pass. Do not use that policy for replies, manual outreach, follow-ups, or any non-website source.

For general outbound, use this workflow every time:
1. create or inspect the draft
2. run lifecycle preflight: `shm send --dry-run ... --account EMAIL`
3. open that exact draft in a CDP-enabled Superhuman app without editing it
4. optionally run local `shm draft attest-render ...` for observation only; it is never approval authority
5. request approval using account/thread/draft/delay semantics only; the broker obtains its authoritative render from the trusted prepare socket
6. review the complete trusted Slack presentation, both PNG roles, and attachment digests
7. obtain the short-lived Ed25519 receipt bound to that exact `approval_binding`
8. let the trusted executor run `shm send --confirm ... --approval-receipt RECEIPT`; use `shm executor status|abort RECEIPT_ID` during grace and require `state: provider_confirmed`

Never treat draft timestamps, labels, HTTP acceptance, `sent_backend_confirmed`, or exit `4` as a completed send. Never create CRM/follow-up work until `state: sent_provider_confirmed`, `provider_confirmed: true`, `outbound_evidence: true`, and `sent: true`. Caller-supplied `--approval-ref` never authorizes a general send. `shm draft get` and conditional `shm draft send` are signed credential-bridge internals, not agent-callable fallback commands.

For the qualified website-inbound route, pass the exact lead and body-free source case. The command permits only a new compose to exactly one matching `To` recipient, with no Bcc, attachment, or scheduled send. Its account+draft attempt journal claims one POST before network I/O; a pending/unknown result must be reconciled with `shm send status`, never retried with another draft or POST.

## Command surface

All commands return the same JSON envelope:

```json
{"status": "succeeded", "command": "...", "data": {...}, "errors": [], "warnings": []}
```

Current commands:

### Production reader scan

```bash
shm reader scan --since 2026-01-01T00:00:00Z --before 2026-01-02T00:00:00Z \
  [--account exact@example.com ...] [--projection metadata|full] \
  [--thread exact-id ...] [--person exact@example.com ...]
```

Use this command for bounded unified-inbox ingestion. Omitted `--account` means all configured accounts. UTC `Z` bounds are exact and define `[since,before)`. Values OR within repeated `--thread` or `--person`; categories AND together. Person matching normalizes email case and checks From/To/Cc/Bcc exactly.

Prefer the default `metadata` projection unless message content is necessary. Metadata recursively excludes subjects, bodies, snippets, display names, and filenames and never queries FTS. `full` includes only bounded direct-cache content with explicit coverage and provenance; never infer complete body content from a snippet, missing value, or FTS. Treat `LOCAL_CACHE_COVERAGE_ONLY` as a permanent provider warning. Check top-level and per-account coverage/truncation reasons before relying on completeness. No cursor is expected for the single deterministic bounded observed set.

The reader makes an anonymous 0600 immutable/query-only snapshot per account, opens SQLite through its verified descriptor, and fails the whole command if any selected account cannot be read safely. It includes spam/trash. It does not mutate mail or expose attachment bytes/paths. Use `shm schema reader.scan` for current fixed caps and contract details.

### Thread commands

```bash
shm thread messages <thread_id>
shm thread userdata <thread_id>
shm thread list [--limit N] [--unread] [--participants] [--fail-empty] [--account email]
shm thread search <query> [--limit N] [--unread] [--participants] [--fail-empty] [--account email]
```

### Opens / read receipts

```bash
shm opens <thread_id> [--recipient email]
shm opens --recent [--limit N] [--recipient email]
```

Rules:
- provide **either** `<thread_id>` **or** `--recent`
- not both
- `--recipient` works in both modes

### Draft commands

Body can be provided inline, from a file, or via stdin:
- `--body "text"` — inline
- `--body-file ./path.txt` — read from file
- `--body -` — read from stdin (pipe)

Same for HTML body: `--body-html`, `--body-html-file`, `--body-html -`.

```bash
shm draft reply <thread_id> --body "..." [--body-html html] [smart-send flags]
shm draft reply <thread_id> --body-file ./reply.txt [smart-send flags]
echo "body" | shm draft reply <thread_id> --body - [smart-send flags]
shm draft reply-all <thread_id> --body "..." [--body-html html] [smart-send flags]
shm draft forward <thread_id> --body "..." [--to email ...] [--cc email ...] [--bcc email ...] [--body-html html] [smart-send flags]
shm draft compose --subject "..." --body "..." [--to email ...] [--cc email ...] [--bcc email ...] [--body-html html] [smart-send flags]
shm draft read <thread_id> [--draft-id id] [--active-only] [--account email]
shm draft status <thread_id> [--draft-id id] [--account email]
shm draft attest-render <thread_id> <draft_id> --account email --output private-dir [--window-id id]
shm draft discard <thread_id> <draft_id>
shm draft attach <thread_id> <draft_id> <file> [--content-type mime]
shm draft share <thread_id> <draft_id> [--name name]
shm draft unshare <thread_id> <draft_id>
```

**Smart-send flags** (available on reply, reply-all, forward, compose):

| Flag | Purpose |
|---|---|
| `--scheduled-for <iso>` | Schedule send for a future time (ISO datetime) |
| `--abort-on-reply` | Cancel scheduled send if someone replies first |
| `--reminder <iso>` | Set a follow-up reminder (ISO datetime) |
| `--sensitivity-label-id <id>` | Microsoft sensitivity label |
| `--sensitivity-tenant-id <id>` | Microsoft sensitivity tenant |

### Comment commands

```bash
shm comment post <thread_id> --body "..." [--mention EMAIL NAME]
shm comment read <thread_id>
shm comment discard <thread_id> <comment_id>
```

### Send / misc

```bash
shm send --dry-run <thread_id> <draft_id> --account email
shm attestation show <id-or-path> --account email --thread-id <thread_id> --draft-id <draft_id>
shm approval verify <receipt.json> --attestation <id-or-path>
shm send status <thread_id> <draft_id> --account email [--wait seconds]
# The trusted executor invokes this only with an externally signed exact receipt:
shm send --confirm <thread_id> <draft_id> --account email --approval-receipt <receipt.json> [--wait seconds]
# Designated qualified website-inbounds automation only:
shm send --qualified-website-inbound <thread_id> <draft_id> --account email \
  --lead-email lead@example.com --qualification-ref website-inbounds:webin-0123abcd [--wait seconds]
shm setup [--config path] [--email address]
shm doctor
shm schema [command]
```

## Recommended workflow patterns

### 1. Reply to an email in Superhuman

If you do not know the thread ID yet:

```bash
shm thread search "customer name topic"
```

Then:

```bash
shm thread messages <thread_id>
shm draft reply <thread_id> --body "..."
shm send --dry-run <thread_id> <draft_id> --account owner@example.com
shm draft attest-render <thread_id> <draft_id> --account owner@example.com --output ./private-preview
shm attestation show <attestation_id> --account owner@example.com --thread-id <thread_id> --draft-id <draft_id>
```

The external broker accepts only account/thread/draft/delay semantics, obtains its authoritative attestation from the trusted prepare socket, and issues a single-use receipt after explicit approval. Then let the credential-isolated executor consume that receipt; do not supply evidence bytes or invoke local/raw transport. Use `shm executor status RECEIPT_ID` during grace/reconciliation and require `sent_provider_confirmed`. This general flow is separate from the qualified website-inbound policy above.

### 2. Get read receipts / recent opens

Per thread:

```bash
shm opens <thread_id>
shm opens <thread_id> --recipient someone@example.com
```

Across threads:

```bash
shm opens --recent --limit 10
shm opens --recent --recipient someone@example.com
```

### 3. Schedule a follow-up with attachment

This is a multi-step flow — create the draft first to get the draft_id, then attach:

```bash
# 1. Create a scheduled reply
shm draft reply <thread_id> --body "As discussed, see attached." --scheduled-for "2026-03-26T09:00:00Z"
# → note the draft_id from the response

# 2. Attach file(s)
shm draft attach <thread_id> <draft_id> ./proposal.pdf --content-type application/pdf

# 3. Verify
shm send --dry-run <thread_id> <draft_id>
```

To cancel the send if someone replies before the scheduled time, add `--abort-on-reply`:

```bash
shm draft reply <thread_id> --body "Following up..." --scheduled-for "2026-03-26T09:00:00Z" --abort-on-reply
```

### 4. Share a draft for team review

```bash
shm draft reply <thread_id> --body "..."
shm draft share <thread_id> <draft_id>
```

Use `draft unshare` to revoke it later.

### 5. Read raw thread metadata only when needed

Prefer the specialized commands first:
- `thread messages`
- `draft read`
- `comment read`
- `opens`

Use raw thread userdata only for advanced/debug cases:

```bash
shm thread userdata <thread_id>
```

## How to choose between Gmail and Superhuman

Use `shm` when the user wants Superhuman-specific behavior such as:
- working from their Superhuman cache
- read receipts / Recent Opens
- draft share / unshare
- comments on Superhuman threads
- reverse-engineered Superhuman workflows

Do **not** use `shm` when the user explicitly wants Gmail, `gog gmail`, or a Google-native workflow.

## Error handling hints

If a command fails:
- `auth` → run `shm doctor`, restart Superhuman if needed
- `network` → retry if `retryable: true`
- `input` → check thread id / draft id / flags
- `not-found` → search/list first to confirm IDs

## Quick examples

```bash
# Find a thread
shm thread search "kalgin follow up"

# Read it
shm thread messages 19c76b5e86217b7b

# Check opens
shm opens 19c76b5e86217b7b
shm opens --recent --limit 5

# Draft + share
shm draft reply 19c76b5e86217b7b --body "Thanks — following up here."
shm draft share 19c76b5e86217b7b draft00abc123

# Safe send flow
shm send --dry-run 19c76b5e86217b7b draft00abc123 --account owner@example.com
shm draft attest-render 19c76b5e86217b7b draft00abc123 --account owner@example.com --output ./private-preview
shm attestation show ATTESTATION_ID --account owner@example.com --thread-id 19c76b5e86217b7b --draft-id draft00abc123
# ... show exact result, obtain explicit approval, then use configured send gate ...
shm send status 19c76b5e86217b7b draft00abc123 --account owner@example.com --wait 120
```
