# Superhuman AI Endpoints

Last updated: 2026-03-23

## Scope

AI-related endpoints from the Superhuman web bundle. None live-validated yet.

---

## 1. `ai.compose`

Core AI draft composition. Generates email body text based on instructions and thread context.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/ai.compose` |
| **Response** | **Streaming** (ReadableStream) |

### Request body

```json
{
  "instructions": "Reply saying we can meet on Tuesday",
  "draft_action": "reply",
  "draft_content": "existing draft text if any",
  "draft_content_type": "text" | "markdown",
  "thread_content": "full thread text",
  "subject": "Re: Meeting next week",
  "to": ["alice@example.com"],
  "cc": ["bob@example.com"],
  "bcc": [],
  "thread_id": "<thread_id>",
  "last_message_id": "<message_id>"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `instructions` | string | User's instruction for what to compose |
| `draft_action` | string | `"reply"`, `"reply-all"`, `"forward"`, `"compose"` |
| `draft_content` | string | Existing draft body (for edit/continue flows) |
| `draft_content_type` | string | `"text"` or `"markdown"` |
| `thread_content` | string | Full thread text for context |
| `subject` | string | Email subject |
| `to` / `cc` / `bcc` | string[] | Recipient lists |
| `thread_id` | string | Thread ID |
| `last_message_id` | string | ID of the message being replied to |

### Response

Streaming text. Read via `ReadableStream.getReader()`, consumed by `_streamResponse()`.

### Who calls it

- Used as fallback when the agentic compose path is not available
- Called from the compose AI flow (⌘+J or "Write with AI")

### Confidence: High

Request shape is fully visible from the `aiCompose()` method.

---

## 2. `ai.composeAgentic`

Agentic AI compose — the newer, more capable compose path with tool use and multi-turn capabilities.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/ai.composeAgentic` |
| **Response** | **Streaming** (SSE with structured JSON events) |

### Request body

```json
{
  "instructions": "Schedule a meeting with them next week",
  "session_id": "<agent_session_id>",
  "content": "existing draft content",
  "content_type": "text" | "markdown",
  "thread_id": "<thread_id>",
  "last_message_id": "<message_id>",
  "thread_content": "full thread text",
  "subject": "Re: Meeting",
  "to": ["alice@example.com"],
  "cc": [],
  "bcc": [],
  "action_type": "reply" | "reply-all" | "compose",
  "interactive": true,
  "selected_text": "optional selected text for inline edit",
  "retry_count": 0,
  "draft_id": "<draft_id>"
}
```

### Request headers (extra)

```
x-superhuman-request-id: <generated_uuid>
```

### Response — SSE stream

Each event is a JSON object. Special events:

- `data: END` — stream complete
- Error events contain an `error` field and are detected by `isSSEErrorEvent()`
- Events include fields like `process`, `tool`, `retrievals`

Error metadata fields observed:
- `code`, `debug`, `stack`, `errorType`, `timestamp`

### Who calls it

- Primary AI compose path when `canUseAIAgent()` returns true
- Used for "Write with AI" and the sidebar AI agent
- Falls back to `ai.compose` if agentic path errors

### Relationship to agent sessions

The `session_id` links to agent session storage. Events from this endpoint are persisted to agent sessions via `writeAgentSession` and `amendAgentSessionEvent`.

### Confidence: High

Full request shape visible. SSE streaming protocol partially visible.

---

## 3. `ai.composeEdit`

Edit an existing draft using AI instructions.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/ai.composeEdit` |
| **Response** | **Streaming** (SSE) |

### Request body

```json
{
  "action_type": "shorten" | "lengthen" | "more_formal" | "more_casual" | "custom",
  "content": "current draft body",
  "content_type": "text" | "markdown",
  "thread_id": "<thread_id>",
  "last_message_id": "<message_id>",
  "to": ["alice@example.com"],
  "cc": [],
  "bcc": [],
  "session_id": "<agent_session_id>",
  "local_datetime": "2026-03-23T10:00:00+00:00",
  "question_event_id": "<event_id>",
  "instructions": "Make it more concise",
  "selected_text": "optional - only the selected portion to edit",
  "retry_count": 0,
  "draft_id": "<draft_id>"
}
```

### Request headers (extra)

```
x-superhuman-request-id: <generated_uuid>
```

### Who calls it

- "Edit with AI" actions: shorten, lengthen, change tone, custom instructions
- Can target the full draft body or just a selected portion

### Confidence: High

---

## 4. `ai.analyzeWritingStyle`

Analyzes the user's writing style from their sent emails. Used during AI onboarding to learn the user's "voice".

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/ai.analyzeWritingStyle` |
| **Method** | POST |
| **Body** | None |

### Behavior

- No request body — the backend analyzes the user's sent emails server-side
- Triggered during AI setup ("Setting up Superhuman AI…" flow)
- Result is stored as `aiVoice` in the user's settings
- Has a 5-minute timeout; shows "RETRY" button on failure
- The `aiVoicePending` flag is set after the call succeeds, while analysis runs server-side

### Who calls it

- `_analyzeWritingStyle()` in the AI setup sidebar tooltip
- Called once during AI onboarding

### Confidence: Medium

---

## 5. `ai.askAIProxy`

The main "Ask AI" / semantic search endpoint. Powers the conversational AI sidebar.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/ai.askAIProxy` |
| **Response** | **SSE** (Server-Sent Events via `fetchEventSource`) |

### Request headers

```
Connection: keep-alive
Content-Encoding: none
Cache-Control: no-cache, no-transform
Content-Type: application/json
Accept: text/event-stream
x-superhuman-session-id: <session_id>
x-superhuman-user-email: <email>
x-superhuman-request-id: <uuid>
x-superhuman-device-id: <device_id>
x-superhuman-version: 2026-03-19T19:05:58Z
Authorization: Bearer <id_token>
```

### Request body

```json
{
  "session_id": "<agent_session_id>",
  "question_event_id": "<event_id>",
  "query": "When is my next meeting with Alice?",
  "chat_history": [],
  "user": {
    "provider_id": "<google_or_microsoft_id>",
    "email": "user@example.com",
    "name": "Thomas Mustier",
    "company": "Nexcade",
    "position": "Founding Commercial"
  },
  "local_datetime": "2026-03-23T10:00:00+00:00",
  "current_thread_id": "<thread_id_if_viewing>",
  "current_thread_messages": "<messages_if_viewing>",
  "available_skills": ["search_emails", "search_calendar", ...]
}
```

### Response — SSE events

- `data: END` — stream complete
- Each event is a JSON object parsed from the SSE `data:` field
- Error events trigger `_logSSEErrorEvent()`

### Available skills

The `available_skills` field tells the backend which tools the AI can use. Observed skill names include:
- `search_emails`
- `search_calendar`
- (others not fully visible in the bundle — the list comes from the client's feature flags)

### Who calls it

- The "Ask AI" sidebar (⌘+K or click the AI button)
- Conversational multi-turn AI queries

### Confidence: High

The full request shape and headers are completely visible.

---

## 6. `ai.tldrSync`

Generates thread summaries, suggested replies, and triage actions for a thread.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/ai.tldrSync` |
| **Method** | POST |

### Request body

```json
{
  "threadId": "<thread_id>",
  "manuallyTriggered": false,
  "forceProcess": false
}
```

### Who calls it

- `generateSummariesRepliesTriages()` — called for thread summarization
- Skips draft threads (`isDraftId` check)

### Confidence: Medium

---

## 7. `ai.enableTLDR`

Enables the TLDR / auto-summary feature and backfills summaries for existing threads.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/ai.enableTLDR` |
| **Method** | POST |
| **Body** | None |

### Confidence: Medium

---

## 8. `ai.reprocessTLDR`

Regenerates AI auto-responses/summaries. Called `regenerateAutoResponses()` in the codebase.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/ai.reprocessTLDR` |
| **Method** | POST |
| **Body** | None |

### Confidence: Medium

---

## 9. `ai.populateOccupation`

Infers a contact's occupation from their name.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/ai.populateOccupation` |
| **Method** | POST |

### Request body

```json
{
  "fullName": "Alice Smith"
}
```

### Confidence: High

---

## 10. `ai.semanticSearchProxy.user`

Warms up / fetches the user's semantic search profile.

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/ai.semanticSearchProxy.user/<provider_id>?local_datetime=...` |
| **Method** | GET |

### Query parameters

| Param | Type | Description |
|-------|------|-------------|
| `local_datetime` | string | User's local datetime (moment.js format) |

### Prerequisites

- Feature flag: `ai.ask_ai` must be available
- `canUseAIAgent()` must return true

### Confidence: Medium

---

## 11. `ai.semanticSearchProxy.suggestions`

Gets search suggestions for the AI sidebar.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/ai.semanticSearchProxy.suggestions` |
| **Method** | POST |

### Request body

```json
{
  "session_id": "<agent_session_id>",
  "query": "search query",
  "name": "Thomas Mustier",
  "company": "Nexcade",
  "chat_history": [],
  "retrievals": []
}
```

Supports `AbortController` signal for cancellation.

### Confidence: Medium

---

## 12. `ai.enable` / `ai.disable`

Toggle AI features on/off for the account.

| Endpoint | Method | Body |
|----------|--------|------|
| `POST /~backend/v3/ai.enable` | POST | None |
| `POST /~backend/v3/ai.disable` | POST | None |

### Confidence: High

---

## Summary table

| Endpoint | Method | Streaming | Body | Confidence |
|----------|--------|-----------|------|------------|
| `ai.compose` | POST | ✅ | Full thread + instructions | High |
| `ai.composeAgentic` | POST | ✅ SSE | Full thread + instructions + session | High |
| `ai.composeEdit` | POST | ✅ SSE | Draft content + edit instructions | High |
| `ai.analyzeWritingStyle` | POST | ❌ | None | Medium |
| `ai.askAIProxy` | POST | ✅ SSE | Query + user context + skills | High |
| `ai.tldrSync` | POST | ❌ | Thread ID | Medium |
| `ai.enableTLDR` | POST | ❌ | None | Medium |
| `ai.reprocessTLDR` | POST | ❌ | None | Medium |
| `ai.populateOccupation` | POST | ❌ | Full name | High |
| `ai.semanticSearchProxy.user` | GET | ❌ | Query params | Medium |
| `ai.semanticSearchProxy.suggestions` | POST | ❌ | Session + query | Medium |
| `ai.enable` | POST | ❌ | None | High |
| `ai.disable` | POST | ❌ | None | High |
