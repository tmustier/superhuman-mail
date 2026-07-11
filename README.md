# superhuman-mail

Unofficial, reverse-engineered Superhuman mail client with an agent-friendly CLI.

This is **not** an official SDK. It talks to Superhuman's private API and local desktop cache.

## What it does

- **Threads**: search, list, and read cached thread messages
- **Read receipts / opens**: inspect per-thread opens and the local Recent Opens feed
- **Drafts**: create reply, reply-all, forward, and compose drafts
- **Draft management**: read, discard, attach files, share, and unshare drafts
- **Comments**: post, read, and discard thread comments
- **Send safety**: typed lifecycle, exact live-render attestation, local attempt reconciliation, and provider-confirmed completion
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

If multiple Superhuman accounts are signed in, choose one explicitly:

```bash
shm setup --email someone@example.com
```

To use a config somewhere else:

```bash
shm setup --config /path/to/config.json
export SUPERHUMAN_MAIL_CONFIG=/path/to/config.json
```

## Requirements

- Superhuman desktop app installed and signed in
- Python 3.11+
- Node.js 22+ for the CDP exact-render probe
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

If Superhuman updates or you switch accounts, just run `shm setup` again. For multi-account setups, pass `--email`.

## CLI

Every command returns the same JSON envelope:

```json
{"status": "succeeded", "command": "...", "data": {...}, "errors": [], "warnings": []}
```

There is no alternate text/table mode. Humans can pipe to `jq`; agents always get the same shape.

### Safety tiers

| Tier | Commands | Risk |
|---|---|---|
| **read** | `thread messages`, `thread userdata`, `thread list`, `thread search`, `opens`, `opens --recent`, `draft read`, `draft status`, `draft attest-render`, `attestation show`, `send --dry-run`, `send status`, `comment read`, `doctor`, `schema` | No mail mutation |
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
shm opens --recent --recipient recipient@example.com

# Create drafts
shm draft reply <thread_id> --body "Thanks for the update"
shm draft reply-all <thread_id> --body "Sounds good"
shm draft forward <thread_id> --body "FYI" --to someone@example.com
shm draft compose --subject "Hello" --body "Hi there" --to someone@example.com

# Smart-send options (available on all draft creation commands)
shm draft reply <thread_id> --body "Following up" --scheduled-for "2026-03-26T09:00:00Z"
shm draft reply <thread_id> --body "Checking in" --scheduled-for "2026-03-26T09:00:00Z" --abort-on-reply
shm draft compose --subject "Hi" --body "..." --to x@example.com --reminder "2026-04-01T09:00:00Z"

# Read / manage drafts
shm draft read <thread_id> [--active-only] [--account email]
shm draft status <thread_id> [--draft-id id] [--account email]
shm draft discard <thread_id> <draft_id>
shm draft attach <thread_id> <draft_id> ./report.pdf
shm draft share <thread_id> <draft_id>
shm draft unshare <thread_id> <draft_id>

# Comments
shm comment post <thread_id> --body "Please review"
shm comment read <thread_id>
shm comment discard <thread_id> <comment_id>

# Send safety: lifecycle preflight → exact render → approval gate → confirm/status
shm send --dry-run <thread_id> <draft_id> --account owner@example.com
shm draft attest-render <thread_id> <draft_id> --account owner@example.com --output ./private-preview [--window-id ID]
shm attestation show <id-or-path> --account owner@example.com --thread-id <thread_id> --draft-id <draft_id>
shm approval verify <receipt.json> --attestation <id-or-path>
shm send --confirm <thread_id> <draft_id> --account owner@example.com --attestation <id-or-path> --approval-receipt <receipt.json> --wait 120
shm send status <thread_id> <draft_id> --account owner@example.com --wait 120

# Diagnostics
shm setup [--email someone@example.com]
shm doctor
shm executor-contract
shm schema
shm schema draft.forward
```

### Strict send semantics

`send --dry-run` is metadata/lifecycle validation only; it reports `send_eligible: false` until an exact live-Superhuman render is attested. `send --confirm` requires an externally issued Ed25519 receipt bound to that exact attestation, reruns the renderer after the trusted executor's grace period, atomically consumes the single-use receipt with the local POST claim, and posts only freshly probed matching bytes. Caller-supplied `--approval-ref` never authorizes.

Only `state: sent_provider_confirmed` returns `sent: true`. Accepted/pending/unknown/backend-only or inconsistent possible-send outcomes exit `4`; definitely rejected/failed outcomes exit `1`; provider-confirmed or explicitly scheduled outcomes exit `0`. The local journal prevents duplicate POSTs only among cooperating trusted-executor processes sharing one state directory—global exactly-once is not claimed. Until an external issuer public key is pinned and the transport credentials are isolated in a trusted executor, confirm fails closed with `APPROVAL_TRUST_UNAVAILABLE`.

See [`docs/send-safety.md`](docs/send-safety.md) for renderer setup, lifecycle evidence, redaction, and retry rules.

### Isolated authority services

Public source for the Slack issuer, Ed25519 signer, 60-second cancellable send executor, and native credential bridge lives in [`authority/`](authority/README.md). They build as three separately signed artifacts with independent sockets, service identities, Keychain ACLs, release pins, and revocation paths.

`shm executor-contract` is credential-free. `shm draft get` and conditional `shm draft send` are fixed provider-bridge operations for the signed executor only; agents must not invoke them directly or treat them as a raw-send fallback.

### Notes

- `thread userdata` is intentionally marked **advanced**. Prefer purpose-built commands like `draft read`, `draft status`, `send status`, `comment read`, or `opens` when possible.
- `thread list` and `thread search` support `--account` for multi-account setups.
- `thread list` and `thread search` support `--fail-empty` to exit with code `3` on zero results.
- All draft creation commands support smart-send flags: `--scheduled-for`, `--abort-on-reply`, `--reminder`, `--sensitivity-label-id`, `--sensitivity-tenant-id`. Use `shm schema draft.reply` for details.
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
from pathlib import Path
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
result = c.draft.create_reply("19d001f35612a211", body="Following up",
                              scheduled_for="2026-03-26T09:00:00Z", abort_on_reply=True)
result = c.draft.create_compose(subject="Hi", body="Hello", to=["someone@example.com"])
result = c.draft.share("19d001f35612a211", "draft00abc123")

# Send (strict exact-attested execution)
result = c.send.validate("19d001f35612a211", "draft00abc123", account="owner@example.com")
attested = c.draft.attest_render("19d001f35612a211", "draft00abc123",
                                 account="owner@example.com", output_dir=Path("./private-preview"))
receipt = c.approval.verify("receipt.json", attestation=attested["attestation_id"])
result = c.send.execute("19d001f35612a211", "draft00abc123",
                        account="owner@example.com",
                        attestation=attested["attestation_id"],
                        approval_receipt="receipt.json")
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
- `docs/draft-lifecycle-render-attestation.md` — RCA and lifecycle/render-attestation design
- `docs/send-safety.md` — lifecycle evidence, exact render attestation, external approval, reconciliation, and exit contracts
- `docs/approval-receipt-issuer-contract.md` / `approval-receipt-v1.schema.json` — trusted issuer/executor interface
- `authority/README.md` — isolated issuer, signer, executor, credential bridge, release, and activation gates

## Safety

- `send` is irreversible and requires exact attestation plus an externally signed, short-lived, exact-binding approval receipt
- execute-time lifecycle validation blocks terminal source-draft residue and existing pending/scheduled jobs
- HTTP acceptance is pending, never proof of delivery; provider-confirmed immutable identity is required for `sent: true`
- retries reuse one local attempt identity and reconcile before any further action
- draft/comment/share operations are reversible
- `shm doctor` verifies config, local DB, keychain, and auth before you rely on the CLI

## License

MIT
