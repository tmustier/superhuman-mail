# Superhuman Agent Session Endpoints

Last updated: 2026-03-23

## Scope

Agent session endpoints are part of Superhuman's AI agent infrastructure. They manage persistent conversation sessions between the user and Superhuman's AI, storing the full history of events (user messages, agent responses, tool calls).

These are **new endpoints** — they underpin the "Ask AI" sidebar and the agentic compose features.

---

## Data model

Agent sessions are stored in Superhuman's userdata at:

```
users/<providerId>/agentSessions/<sessionId>
```

### Session object

```json
{
  "agentSessionId": "<uuid>",
  "events": [
    {
      "agentSessionId": "<uuid>",
      "speaker": "user",
      "payload": {
        "event_id": "<uuid>",
        "session_id": "<uuid>",
        "content": "When is my next meeting?",
        "finished": true
      }
    },
    {
      "agentSessionId": "<uuid>",
      "speaker": "agent",
      "payload": {
        "event_id": "<uuid>",
        "in_reply_to_event_id": "<user_event_id>",
        "session_id": "<uuid>",
        "content": "Your next meeting is...",
        "finished": true,
        "process": { ... },
        "tool": { ... },
        "retrievals": [ ... ]
      }
    }
  ],
  "metadata": {},
  "historyId": 0,
  "title": "New Chat",
  "createdAt": "2026-03-23T10:00:00.000Z",
  "updatedAt": "2026-03-23T10:05:00.000Z"
}
```

### Event speakers

- `"user"` — user-initiated messages/instructions
- `"agent"` — AI responses, tool calls, retrievals

### Agent event payload fields

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | string | Unique event ID |
| `in_reply_to_event_id` | string | The user event this responds to |
| `session_id` | string | Session ID |
| `content` | string | The text content |
| `finished` | boolean | Whether this event is complete |
| `process` | object | Optional — process/tool execution details |
| `tool` | object | Optional — tool call details |
| `retrievals` | array | Optional — search results / sources |

### Local persistence

Sessions are also stored locally in a WebSQL/SQLite database with columns:
- `id` — session ID
- `updated_at` — timestamp (numeric)
- `title` — session title
- `json` — full session JSON
- `is_discarded` — 0 or 1

---

## Endpoints

### 1. `userdata.writeAgentSession`

Write or update agent session data. Used for multiple operations.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/userdata.writeAgentSession` |
| **Method** | POST |

#### Operations

**Discard a session:**
```json
{
  "path": "users/<providerId>/agentSessions/<sessionId>/discardedAt",
  "value": "2026-03-23T10:00:00.000Z"
}
```

**Restore a discarded session:**
```json
{
  "path": "users/<providerId>/agentSessions/<sessionId>/discardedAt",
  "value": null
}
```
Also writes `updatedAt`:
```json
{
  "path": "users/<providerId>/agentSessions/<sessionId>/updatedAt",
  "value": "2026-03-23T10:00:00.000Z"
}
```

**Set session metadata:**
```json
{
  "path": "users/<providerId>/agentSessions/<sessionId>/metadata",
  "value": { ... }
}
```

### Who calls it

- `discardAgentSession(sessionId)` — marks a session as deleted
- `restoreAgentSession(sessionId, updatedAt)` — undeletes a session
- `setAgentSessionMetadata(sessionId, metadata)` — stores arbitrary metadata

### Confidence: High

All three paths are fully visible.

---

### 2. `userdata.amendAgentSessionEvent`

Amend (update) a specific event within an agent session. Used when the user edits an AI-generated draft.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/userdata.amendAgentSessionEvent` |
| **Method** | POST |

#### Request body

```json
{
  "path": "users/<providerId>/agentSessions/<sessionId>/events/<eventId>",
  "value": {
    "agentSessionId": "<uuid>",
    "speaker": "agent",
    "payload": {
      "event_id": "<new_event_id>",
      "in_reply_to_event_id": "<user_event_id>",
      "session_id": "<uuid>",
      "content": "updated content after user edit",
      "finished": true
    }
  }
}
```

#### Response

```json
{
  "historyId": 42
}
```

The returned `historyId` is used to track the session version.

### Who calls it

- When the user edits an AI-composed draft and the modification needs to be persisted back to the session history
- Called from the compose UI after AI draft content is modified
- On error, logs `"Failed to persist modified WWAI v2 event to backend"`

### Confidence: High

Request and response shapes are both visible.

---

### 3. `userdata.promoteAgentSession`

Promote a "chip" (suggested action) from an agent session. This is how the user accepts/selects one of the AI's suggestions.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/userdata.promoteAgentSession` |
| **Method** | POST |

#### Request body

```json
{
  "agentSessionId": "<session_id>",
  "chipIndex": 0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `agentSessionId` | string | The session containing the chip |
| `chipIndex` | number | Index of the selected chip/suggestion |

### Behavior

After promoting:
1. The session sidebar opens with `shouldOpenSidebar: true`
2. Sources are loaded with `loadingSources: true`
3. Waits for a session update (up to 30 seconds) to load expanded sources
4. If no update arrives within 30s, cleans up the pending sources state

### Who calls it

- When the user clicks a suggested action chip in the AI response
- The "Responses" feature in thread view

### Confidence: High

---

## Architecture notes

### Session lifecycle

1. **Create**: Sessions are created locally first (`startLocalSession`), then synced to the backend
2. **Interact**: User messages and AI responses are added as events
3. **Persist**: Events are persisted via `writeAgentSession` and `amendAgentSessionEvent`
4. **Discard**: Sessions can be soft-deleted via `discardedAt`
5. **Restore**: Discarded sessions can be restored by setting `discardedAt` to `null`

### Relationship to compose

The agentic compose flow (`ai.composeAgentic`) creates an agent session and links events to it. When the user edits an AI draft:
1. The edit event is sent to `ai.composeEdit`
2. The modified event is persisted to the session via `amendAgentSessionEvent`
3. The `historyId` is updated to track the version

### Feature flag

Agent sessions require `canUseAIAgent()` to return true, which checks the `ai.ask_ai` feature flag.

---

## Summary table

| Endpoint | Method | Purpose | Confidence |
|----------|--------|---------|------------|
| `userdata.writeAgentSession` | POST | Create/update/discard/restore sessions + metadata | High |
| `userdata.amendAgentSessionEvent` | POST | Update a specific event in a session | High |
| `userdata.promoteAgentSession` | POST | Accept a suggested action chip | High |
