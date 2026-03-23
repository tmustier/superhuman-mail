# Superhuman Knowledge Base, Links, Search & Sharing Endpoints

Last updated: 2026-03-23

## Scope

Endpoints for the knowledge base (snippets/templates), sender blocking, shared thread links, search history, and thread/draft sharing infrastructure.

---

## Knowledge base

### `knowledgeBase.upsert`

Create or update a knowledge base entity (used by Superhuman AI for context).

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/knowledgeBase.upsert` |
| **Method** | POST |

Body: knowledge base entity object (fields not fully visible — passed through from caller).

### `knowledgeBase.delete`

Delete a knowledge base entity.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/knowledgeBase.delete` |
| **Method** | POST |

Body: entity identifier (passed through from caller).

### Confidence: Low

---

## Sender blocking

### `blocks.create`

Block a sender.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/blocks.create` |

```json
{ "sender": "spam@example.com" }
```

### `blocks.delete`

Unblock a sender.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/blocks.delete` |

```json
{ "sender": "spam@example.com" }
```

### Confidence: High

---

## Shared thread links

### `links.content`

Get the content of a shared thread link.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/links.content` |
| **Auth** | CSRF token + guest token |

```json
{
  "path": "<shared_path>",
  "guestToken": "<token>",
  "lastUpdatedAt": "2026-03-23T10:00:00.000Z"
}
```

Called from the anonymous shared link viewer (no Superhuman account needed). Uses a CSRF token from `csrfToken.create`.

### `links.comment`

Post a comment on a shared thread using an authenticated user's ID token.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/links.comment` |
| **Auth** | ID token |

Body: comment object (passed through).

### `links.commentToken`

Post a comment on a shared thread using a guest token (no account needed).

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/links.commentToken` |
| **Auth** | Guest token |

Body: comment object including `guestToken`.

### `links.open`

Open/track a shared thread link.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/links.open` |

```json
{ "path": "<shared_path>" }
```

### Confidence: Medium

---

## Search history

### `userdata.searchHistory`

Persist a search history entry.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/userdata.searchHistory` |
| **Method** | POST |

```json
{
  "value": "search query text",
  "type": "query" | "contact" | "ai",
  "relativeTimestamp": -1234567
}
```

| Field | Type | Description |
|-------|------|-------------|
| `value` | string | The search text (trimmed) |
| `type` | string | `"query"`, `"contact"`, or `"ai"` |
| `relativeTimestamp` | number | Negative microsecond offset from `Date.now()` — Superhuman uses this format instead of absolute timestamps |

The response may include updated search history settings from the backend.

### Search history types

The local search history stores three types:
- `query` — text searches
- `contact` — contact/email searches
- `ai` — "Ask AI" queries (shown at top of search suggestions)

### Confidence: High

---

## Thread / draft sharing (containers)

### `containers.share`

Share a thread with team members.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/containers.share` |

```json
{
  "threadId": "<thread_id>",
  "path": "<path>",
  "add": ["teammate@example.com"],
  "metricsMetadata": { ... },
  "sharerName": "Thomas Mustier"
}
```

### `containers.unshare`

Unshare a thread.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/containers.unshare` |

```json
{ "path": "<shared_path>" }
```

### Confidence: Medium

---

## Userdata sharing

### `userdata.share`

Share userdata (used for sharing draft content with team, distinct from `drafts.share`).

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/userdata.share` |

```json
{
  "path": "users/<providerId>/threads/<threadId>/messages/<messageId>",
  "name": "Your Name"
}
```

Called from `shareDraft()` when the `useUserdataShare` flag is true. Otherwise falls back to `drafts.share`.

### `userdata.unshare`

Unshare userdata.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/userdata.unshare` |

```json
{
  "path": "users/<providerId>/threads/<threadId>/messages/<messageId>"
}
```

Called from `unshareDraft()` when the `useUserdataUnshare` flag is true. Otherwise falls back to `drafts.unshare`.

### Note on `userdata.share` vs `drafts.share`

Both endpoints exist. `userdata.share`/`userdata.unshare` appear to be the newer implementation, gated behind a feature flag. `drafts.share`/`drafts.unshare` are the older path (already validated in this repo).

### Confidence: Medium

---

## Summary table

| Endpoint | Method | Purpose | Confidence |
|----------|--------|---------|------------|
| `knowledgeBase.upsert` | POST | Create/update knowledge base entity | Low |
| `knowledgeBase.delete` | POST | Delete knowledge base entity | Low |
| `blocks.create` | POST | Block a sender | High |
| `blocks.delete` | POST | Unblock a sender | High |
| `links.content` | POST | Get shared link content (guest auth) | Medium |
| `links.comment` | POST | Comment on shared thread (ID token) | Medium |
| `links.commentToken` | POST | Comment on shared thread (guest token) | Medium |
| `links.open` | POST | Open/track shared link | Medium |
| `userdata.searchHistory` | POST | Persist search history entry | High |
| `containers.share` | POST | Share thread with team | Medium |
| `containers.unshare` | POST | Unshare thread | Medium |
| `userdata.share` | POST | Share userdata (newer draft sharing) | Medium |
| `userdata.unshare` | POST | Unshare userdata | Medium |
