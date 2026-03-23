# superhuman-mail

Unofficial, reverse-engineered Superhuman mail client with an agent-friendly CLI.

This is **not** an official SDK. It talks to Superhuman's private API and local desktop cache.

## What it does

- **Threads**: search, list, and read cached thread messages
- **Read receipts / opens**: inspect per-thread opens and the local Recent Opens feed
- **Drafts**: create reply, reply-all, forward, and compose drafts
- **Draft management**: read, discard, attach files, share, and unshare drafts
- **Comments**: post, read, and discard thread comments
- **Send**: validate and send drafts with an explicit safety gate
- **Setup / doctor**: bootstrap config from the local Superhuman app and verify auth

## Install

### As a Pi package

```bash
pi install git:github.com/tmustier/superhuman-mail
```

This installs the `superhuman-mail` skill so agents know when and how to use `shm`.

### CLI setup

```bash
git clone https://github.com/tmustier/superhuman-mail.git
cd superhuman-mail
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
shm setup
shm doctor
```

`shm setup` reads credentials directly from the local Superhuman desktop app. No manual config should be necessary.

To use a config somewhere else:

```bash
shm setup --config /path/to/config.json
export SUPERHUMAN_MAIL_CONFIG=/path/to/config.json
```

## Requirements

- Superhuman desktop app installed and signed in
- Python 3.11+
- `cryptography` installed in the environment running `shm`

## Config

`shm setup` generates `config.json` automatically by reading the local Superhuman app.

It extracts fields like:

- active email account
- author name
- google id
- device id
- team id / shard key
- Superhuman version
- local SQLite DB path

If Superhuman updates or you switch accounts, just run `shm setup` again.

## CLI

Every command returns the same JSON envelope:

```json
{"status": "succeeded", "command": "...", "data": {...}, "errors": [], "warnings": []}
```

There is no alternate text/table mode. Humans can pipe to `jq`; agents always get the same shape.

### Safety tiers

| Tier | Commands | Risk |
|---|---|---|
| **read** | `thread messages`, `thread userdata`, `thread list`, `thread search`, `opens`, `opens --recent`, `draft read`, `comment read`, `doctor`, `schema` | None |
| **write** | `setup`, `draft reply`, `draft reply-all`, `draft forward`, `draft compose`, `draft discard`, `draft attach`, `draft share`, `draft unshare`, `comment post`, `comment discard` | Reversible |
| **irreversible** | `send` | Requires `--dry-run` or `--confirm` |

### Command surface

```bash
# Find threads first if you do not know the thread id
shm thread search "kalgin follow up"
shm thread search "invoice" --unread --limit 5
shm thread list --limit 10
shm thread list --unread --participants

# Read thread data
shm thread messages <thread_id>
shm thread userdata <thread_id>                 # advanced raw thread userdata

# Read receipts / opens
shm opens <thread_id>
shm opens <thread_id> --recipient someone@example.com
shm opens --recent
shm opens --recent --limit 10
shm opens --recent --recipient rcross@kalginglobal.com

# Create drafts
shm draft reply <thread_id> --body "Thanks for the update"
shm draft reply-all <thread_id> --body "Sounds good"
shm draft forward <thread_id> --body "FYI" --to someone@example.com
shm draft compose --subject "Hello" --body "Hi there" --to someone@example.com

# Read / manage drafts
shm draft read <thread_id>
shm draft discard <thread_id> <draft_id>
shm draft attach <thread_id> <draft_id> ./report.pdf
shm draft share <thread_id> <draft_id>
shm draft unshare <thread_id> <draft_id>

# Comments
shm comment post <thread_id> --body "Please review"
shm comment read <thread_id>
shm comment discard <thread_id> <comment_id>

# Send (validate first, then explicitly confirm)
shm send --dry-run <thread_id> <draft_id>
shm send --confirm <thread_id> <draft_id>

# Diagnostics
shm doctor
shm schema
shm schema draft.forward
```

### Notes

- `thread userdata` is intentionally marked **advanced**. Prefer purpose-built commands like `draft read`, `comment read`, or `opens` when possible.
- `thread list` and `thread search` support `--account` for multi-account setups.
- `thread list` and `thread search` support `--fail-empty` to exit with code `3` on zero results.
- `opens` requires exactly one of:
  - `<thread_id>`
  - `--recent`

### Error handling

Errors are structured for agent recovery:

```json
{
  "status": "failed",
  "command": "send",
  "data": null,
  "errors": [{
    "class": "auth",
    "code": "TOKEN_EXPIRED",
    "retryable": true,
    "hint": "Restart Superhuman app or run `shm doctor`"
  }],
  "warnings": []
}
```

Error classes:

- `auth`
- `network`
- `not-found`
- `input`
- `conflict`
- `rate-limit`

## Python client

```python
from superhuman_mail import Client

c = Client()

# Threads
result = c.thread.messages("19d001f35612a211")
result = c.thread.search("kalgin follow up")

# Opens
result = c.opens.per_thread("19d001f35612a211")
result = c.opens.per_thread("19d001f35612a211", recipient="someone@example.com")
result = c.opens.recent(limit=10)

# Drafts
result = c.draft.create_reply("19d001f35612a211", body="Thanks!")
result = c.draft.create_compose(subject="Hi", body="Hello", to=["someone@example.com"])
result = c.draft.share("19d001f35612a211", "draft00abc123")

# Send
result = c.send.validate("19d001f35612a211", "draft00abc123")
result = c.send.execute("19d001f35612a211", "draft00abc123")
```

All methods return the same envelope dict as the CLI.

## Auth model

This repo uses a hybrid auth model:

1. read local Superhuman desktop app state and cookies
2. exchange those for API credentials/tokens
3. call Superhuman backend endpoints directly
4. read local SQLite cache for fast thread / search / recent-opens access

So the Superhuman desktop app must be installed and signed in.

## Repo layout

```text
shm                           # CLI entry point
superhuman_mail/
  __init__.py                 # exports Client
  __main__.py                 # supports python -m superhuman_mail
  _auth.py                    # cookie decrypt + token exchange
  _config.py                  # config loader
  _envelope.py                # JSON envelope helpers
  _local.py                   # local SQLite DB reads
  cli.py                      # CLI implementation
  client.py                   # Python client
  thread.py                   # thread reads / search / list
  opens.py                    # read receipts + recent opens
  draft.py                    # draft CRUD + attachments
  comment.py                  # comment CRUD
  send.py                     # send + validate
  share.py                    # draft share / unshare transport
  setup.py                    # auto-bootstrap config from local app
docs/
  superhuman-api-endpoints.md # reverse-engineered API inventory
  official-superhuman-mcp-beta.md
config.example.json
pyproject.toml
```

## Docs

- `docs/superhuman-api-endpoints.md` — reverse-engineered endpoint inventory
- `docs/superhuman-read-statuses.md` — read receipts, Recent Opens, and the thread userdata model
- `docs/official-superhuman-mcp-beta.md` — notes on the official MCP beta

## Safety

- `send` is irreversible and intentionally requires `--dry-run` or `--confirm`
- draft/comment/share operations are reversible
- `shm doctor` verifies config, local DB, keychain, and auth before you rely on the CLI

## License

MIT
