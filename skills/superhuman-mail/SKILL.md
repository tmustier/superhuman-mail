---
name: superhuman-mail
description: >
  Interact with Superhuman email via the `shm` CLI — create drafts (reply, reply-all, forward, compose),
  post and read comments, upload attachments, share/unshare drafts, read threads, and send email.
  Use when the user asks to draft an email in Superhuman, reply to a thread, comment on a thread,
  send via Superhuman, share a draft, or read messages from their Superhuman inbox.
  Do NOT use for Gmail-only workflows or when the user explicitly wants `gog gmail`.
---

# Superhuman Mail

Interact with Superhuman email using the `shm` CLI. This is an unofficial, reverse-engineered API client — not an official Superhuman SDK.

## Prerequisites

`shm` requires:
1. The **Superhuman desktop app** installed and signed in
2. A **config.json** with auth values extracted from the app (see setup below)
3. Python 3.11+ with the `cryptography` package

## Setup check

Before using any commands, verify the CLI is working:

```bash
shm doctor
```

If `shm` is not on PATH, find it in the package directory:

```bash
# Find the package install location
pi_pkg_dir=$(find ~/.pi/agent/git -path '*/superhuman-mail' -type d 2>/dev/null | head -1)

# Or check the project-local install
pi_pkg_dir=$(find .pi/git -path '*/superhuman-mail' -type d 2>/dev/null | head -1)

# Run from the package directory
cd "$pi_pkg_dir" && ./shm doctor
```

If `shm doctor` fails on the `config` check, the user needs to create `config.json` — this requires manual setup because the values come from Superhuman DevTools. Tell the user:

> To set up superhuman-mail, copy `config.example.json` to `config.json` in the superhuman-mail directory and fill in the values. You need your Google account ID (from Superhuman DevTools → Cookies) and device ID (from Network headers). See the `_help` fields in the example config.

Alternatively, point to an existing config file:

```bash
export SUPERHUMAN_MAIL_CONFIG=/path/to/config.json
```

## Safety rules

1. **Never send without user approval.** Always use `--dry-run` first, show the preview to the user, and only use `--confirm` after explicit approval.
2. Draft, comment, share, and attachment operations are all **reversible** — safe to run without asking.
3. Read operations have no side effects.

## Commands

All commands output a consistent JSON envelope:
```json
{"status": "succeeded", "command": "...", "data": {...}, "errors": [], "warnings": []}
```

Use `shm schema` for the full command list, or `shm schema <command>` for details on a specific command.

### Read operations (always safe)

```bash
shm thread read <thread_id>              # messages from local DB
shm thread userdata <thread_id>          # drafts/comments/metadata from API
shm draft read <thread_id>               # drafts on a thread
shm comment read <thread_id>             # comments on a thread
```

### Create drafts (reversible)

```bash
shm draft reply <thread_id> --body "Thanks for the update"
shm draft reply-all <thread_id> --body "Sounds good"
shm draft forward <thread_id> --body "FYI — see below"
shm draft compose --subject "Hello" --body "Hi there" --to someone@example.com
```

Optional flags: `--body-html`, `--scheduled-for`, `--cc`, `--bcc` (compose only).

### Manage drafts (reversible)

```bash
shm draft discard <thread_id> <draft_id>
shm draft attach <thread_id> <draft_id> ./file.pdf --content-type application/pdf
```

### Comments (reversible)

```bash
shm comment post <thread_id> --body "Please review this"
shm comment post <thread_id> --body "Hey @Dan" --mention dan@example.com "Dan Smith"
shm comment discard <thread_id> <comment_id>
```

### Send (IRREVERSIBLE — requires user approval)

```bash
# Step 1: validate (show to user)
shm send --dry-run <thread_id> <draft_id>

# Step 2: only after user says "send it"
shm send --confirm <thread_id> <draft_id>
```

Running `shm send` without `--dry-run` or `--confirm` is rejected by the CLI.

### Share / unshare (reversible)

```bash
shm share <thread_id> <draft_id>         # returns a collaboration link
shm unshare <thread_id> <draft_id>
```

### Diagnostics

```bash
shm doctor                               # verify config, auth, connectivity
shm schema                               # list all commands with safety tiers
shm schema send                          # describe a specific command
```

## Workflow patterns

### Reply to a thread

1. `shm thread read <thread_id>` — understand the conversation
2. `shm draft reply <thread_id> --body "..."` — create draft
3. `shm send --dry-run <thread_id> <draft_id>` — show preview to user
4. After user approves: `shm send --confirm <thread_id> <draft_id>`

### Draft for team review

1. `shm draft reply <thread_id> --body "..."` — create draft
2. `shm share <thread_id> <draft_id>` — get collaboration link
3. Share the link with the user/team

### Comment on a thread

1. `shm comment post <thread_id> --body "..."` — post comment
2. No approval needed — comments are visible only to the Superhuman team

## Error recovery

If a command fails, check the `errors` array in the response:

- `"class": "auth"` → run `shm doctor`, restart Superhuman app if needed
- `"class": "network"` → check internet, retry if `"retryable": true`
- `"class": "not-found"` → verify the thread_id/draft_id exists
- `"class": "input"` → check command arguments

## Finding thread IDs

Thread IDs are hex strings like `19d001f35612a211`. The user can find them in the Superhuman URL bar, or you can look them up from the local DB if the user describes the email.
