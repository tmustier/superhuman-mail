# Official Superhuman Mail MCP beta vs this repo

Last updated: 2026-03-22

This note captures how the official **Superhuman Mail MCP Server (Beta)** relates to this reverse-engineered repo.

Source article:
- https://help.superhuman.com/hc/en-us/articles/49810745762067-Superhuman-Mail-MCP-Server-Beta

## Short version

The official MCP beta is the supported, higher-level interface.
This repo is the lower-level, unofficial interface.

Today, this repo still matters because it already validates several capabilities that the public MCP beta docs either do not mention or explicitly do not support yet.

## What the official MCP beta documents

From Superhuman's public beta article, the MCP exposes tools such as:

- `query_email_and_calendar`
- `create_or_update_draft`
- `list_email`
- `create_or_update_event`
- `get_availability_calendar`
- `send_email`
- `get_email_thread`
- `update_preferences_email_and_calendar`
- `get_read_statuses`
- `update_email`

The article also says the MCP can:

- search email and events
- draft replies in your voice and tone
- send emails
- create or update calendar events
- summarize important emails and tasks

## What this repo currently validates

This repo currently validates direct private API access for:

- auth bootstrap from the local Superhuman app session
- thread userdata reads
- comments: write / read / discard
- drafts:
  - reply
  - reply-all
  - forward
  - compose
  - discard
- attachments:
  - upload bytes
  - persist attachment metadata
- scheduling:
  - `scheduledFor` draft persistence
- sending:
  - end-to-end self-send via `POST /~backend/messages/send`
- draft sharing:
  - `POST /~backend/v3/drafts.share`

## Where the official MCP looks stronger

The official MCP beta appears better suited for:

- supported agent integrations
- natural-language email and calendar search
- calendar workflows
- inbox/thread mutation via a stable high-level tool surface
- future compatibility if Superhuman changes its private web APIs

## Where this repo is still stronger today

### 1. Comments

The public MCP beta article does not mention comment tools.
This repo already validates comment write / read / discard.

### 2. Attachments

The official beta FAQ says:

> Can I send attachments? Not yet!

By contrast, this repo already validates:

1. `POST /~backend/v3/attachments.upload`
2. separate attachment metadata persistence at:
   - `threads/<threadId>/messages/<draftId>/attachments/<uuid>`

### 3. Low-level draft internals

This repo documents and validates the actual draft storage model:

- `POST /~backend/v3/userdata.writeMessage`
- `threads/<threadId>/messages/<draftId>/draft`

That gives us finer control than the public MCP's high-level `create_or_update_draft` abstraction.

### 4. Draft sharing

This repo validates:

- `POST /~backend/v3/drafts.share`

The public MCP beta article does not document draft sharing.

## Auth / setup observations

The official MCP beta setup requires browser-based authentication.
The public docs describe:

- adding `https://mcp.mail.superhuman.com/mcp` as a remote MCP server
- then running an interactive OAuth sign-in flow in the browser

In our environment we observed:

- `~/.claude.json` stores the MCP server URL, but no readable reusable bearer token
- direct GET to `https://mcp.mail.superhuman.com/mcp` returns:
  - `401 {"detail":"missing-id-token"}`
- the normal Superhuman web-app id token used by the private API is **not** accepted by the MCP endpoint:
  - it returns `401 {"detail":"invalid-token"}`
- the current web bundle contains a `generateMCPToken(...)` symbol, but direct POST to:
  - `POST /~backend/v3/mcp.generateToken`
  returned `404` during our testing

Practical takeaway:

- the official MCP exists and may be useful later
- but it should not block work on this repo
- for now, this repo's direct private API path is the working implementation path

## How this repo authenticates

This repo uses a **hybrid** model:

1. it reads local Superhuman desktop/web state for bootstrap information and cache data
2. it exchanges session state through Superhuman auth endpoints
3. it then talks directly to Superhuman backend endpoints such as:
   - `accounts.superhuman.com`
   - `mail.superhuman.com`

So this is **not** an official public API client like `gog`.
It is a reverse-engineered private API client with local-session bootstrapping.

## Recommendation

For now:

- use the official MCP beta if you want the supported, high-level experience and can complete its OAuth flow in Claude / ChatGPT directly
- use this repo when you need:
  - comments
  - attachments
  - low-level draft manipulation
  - draft sharing
  - reverse-engineering / experimentation

## Likely future direction

If the official MCP matures, the best long-term setup may be:

- official MCP for search / send / calendar / generic agent workflows
- this repo for low-level internals, unsupported features, and validation work
