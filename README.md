# superhuman-mail

Unofficial, reverse-engineered Superhuman mail API client with an agent-friendly CLI.

This is **not** an official SDK. It uses Superhuman's private API, reverse-engineered from the web bundle.

## What it does

- **Drafts**: create reply, reply-all, forward, and compose drafts
- **Comments**: post, read, and discard thread comments
- **Attachments**: upload files to drafts
- **Send**: send drafts (with a mandatory safety gate)
- **Share/Unshare**: draft collaboration links (both validated)
- **Thread reads**: read messages from the local Superhuman DB

## Install

### As a Pi package (recommended for agents)

```bash
pi install git:github.com/tmustier/superhuman-mail
```

This installs the `superhuman-mail` skill, which teaches agents when and how to use `shm`.

You still need to set up the Python CLI (see below).

### CLI setup

```bash
git clone https://github.com/tmustier/superhuman-mail.git
cd superhuman-mail
python3 -m venv .venv
source .venv/bin/activate
pip install -e .         # installs `shm` on PATH
cp config.example.json config.json
# Fill in config.json with your local values (see below)
```

To point `shm` at a config file in a different location:

```bash
export SUPERHUMAN_MAIL_CONFIG=/path/to/config.json
```

Verify everything works:

```bash
shm doctor
```

## Config

Copy `config.example.json` to `config.json` and fill in your values. The config is gitignored.

You need:
- The Superhuman desktop app installed and signed in
- Your Google account ID (from Superhuman DevTools → Cookies)
- Your device ID (from Superhuman DevTools → Network headers)
- Your team ID and shard key (from team settings)

See the `_help` fields in `config.example.json` for where to find each value.

## CLI (`shm`)

Every command outputs a consistent JSON envelope:

```json
{"status": "succeeded", "command": "...", "data": {...}, "errors": [], "warnings": []}
```

### Safety tiers

| Tier | Operations | Risk |
|---|---|---|
| **read** | `thread read`, `thread userdata`, `draft read`, `comment read`, `doctor`, `schema` | None |
| **write** | `draft reply/reply-all/forward/compose/discard/attach`, `comment post/discard`, `share/unshare` | Reversible |
| **irreversible** | `send` | Requires `--dry-run` or `--confirm` |

### Commands

```bash
# Read thread messages from local DB
shm thread read <thread_id>

# Read thread userdata (drafts, comments) from API
shm thread userdata <thread_id>

# Create drafts
shm draft reply <thread_id> --body "Thanks for the update"
shm draft reply-all <thread_id> --body "Sounds good"
shm draft forward <thread_id> --body "FYI"
shm draft compose --subject "Hello" --body "Hi there" --to someone@example.com

# Read / discard drafts
shm draft read <thread_id>
shm draft discard <thread_id> <draft_id>

# Attach files
shm draft attach <thread_id> <draft_id> ./report.pdf

# Comments
shm comment post <thread_id> --body "Please review"
shm comment read <thread_id>
shm comment discard <thread_id> <comment_id>

# Send (requires explicit flag)
shm send --dry-run <thread_id> <draft_id>    # validate only
shm send --confirm <thread_id> <draft_id>    # actually send

# Share / unshare
shm share <thread_id> <draft_id>
shm unshare <thread_id> <draft_id>

# Diagnostics
shm doctor                                    # verify config + auth
shm schema                                    # list all commands
shm schema send                               # describe a specific command
```

### Error handling

Errors are classified with structured metadata:

```json
{
  "status": "failed",
  "command": "draft.create",
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

Error classes: `auth`, `network`, `not-found`, `input`, `conflict`, `rate-limit`

## Python client

```python
from superhuman_mail import Client

c = Client()

# Read
result = c.thread.read("19d001f35612a211")
result = c.comment.read("19d001f35612a211")

# Drafts
result = c.draft.create_reply("19d001f35612a211", body="Thanks!")
result = c.draft.create_compose(subject="Hi", body="Hello", to=["someone@example.com"])
result = c.draft.discard("19d001f35612a211", "draft00abc123")

# Send (validate first, then execute)
result = c.send.validate("19d001f35612a211", "draft00abc123")
result = c.send.execute("19d001f35612a211", "draft00abc123")
```

All methods return the same JSON envelope dict.

## Auth model

This repo uses a **hybrid** auth model:
- Reads session cookies from the local Superhuman desktop app
- Exchanges them for API tokens via Superhuman's auth endpoints
- Then calls Superhuman backend endpoints directly

This means the Superhuman desktop app must be installed and signed in.

## Repo layout

```
shm                           # CLI entry point
superhuman_mail/
  __init__.py                 # exports Client
  _auth.py                    # cookie decrypt + token exchange
  _config.py                  # config loader
  _envelope.py                # JSON envelope helpers
  _local.py                   # local SQLite DB reads
  thread.py                   # thread operations
  draft.py                    # draft CRUD + attachments
  comment.py                  # comment CRUD
  send.py                     # send + validate
  share.py                    # share / unshare
  client.py                   # Client class
  cli.py                      # CLI implementation
docs/
  superhuman-api-endpoints.md # validated API inventory
  official-superhuman-mcp-beta.md
config.example.json
pyproject.toml
```

## Docs

- `docs/superhuman-api-endpoints.md` — full inventory of known Superhuman endpoints with validation status
- `docs/official-superhuman-mcp-beta.md` — comparison with the official Superhuman MCP beta

## Safety

- `send` requires `--dry-run` or `--confirm` — no accidental sends
- All write operations are reversible (drafts can be discarded, comments deleted)
- Structured error responses help agents recover automatically
- `shm doctor` verifies the full auth chain before you start

## License

MIT
