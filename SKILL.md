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
2. Python 3.11+
3. `cryptography` available in the environment running `shm`

## Setup

Bootstrap directly from the local Superhuman app:

```bash
shm setup
shm doctor
```

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

**Never send without explicit user approval.**

Use this workflow every time:
1. create or inspect the draft
2. run `shm send --dry-run ...`
3. show the output to the user
4. only run `shm send --confirm ...` after explicit approval

Everything else in `shm` is either read-only or reversible.

## Command surface

All commands return the same JSON envelope:

```json
{"status": "succeeded", "command": "...", "data": {...}, "errors": [], "warnings": []}
```

Current commands:

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

```bash
shm draft reply <thread_id> --body "..." [--body-html html] [smart-send flags]
shm draft reply-all <thread_id> --body "..." [--body-html html] [smart-send flags]
shm draft forward <thread_id> --body "..." [--to email ...] [--cc email ...] [--bcc email ...] [--body-html html] [smart-send flags]
shm draft compose --subject "..." --body "..." [--to email ...] [--cc email ...] [--bcc email ...] [--body-html html] [smart-send flags]
shm draft read <thread_id> [--draft-id id]
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
shm send --dry-run <thread_id> <draft_id>
shm send --confirm <thread_id> <draft_id>
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
shm send --dry-run <thread_id> <draft_id>
```

After the user explicitly approves:

```bash
shm send --confirm <thread_id> <draft_id>
```

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
shm send --dry-run 19c76b5e86217b7b draft00abc123
# ... wait for explicit approval ...
shm send --confirm 19c76b5e86217b7b draft00abc123
```
