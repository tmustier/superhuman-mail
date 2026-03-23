# Superhuman Messages & Send Flow Endpoints

Last updated: 2026-03-23

## Scope

Endpoints related to sending, cancelling, and logging email sends, plus message label mutation. Supplements the validated send flow in `superhuman-api-endpoints.md`.

---

## 1. `messages/send/cancel`

Cancel (undo) a recently sent email during the undo window.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/messages/send/cancel` |
| **Method** | POST |

### Request body

```json
{
  "draft_message_id": "<draft_id>",
  "draft_thread_id": "<thread_id>",
  "superhuman_id": "<superhuman_id>",
  "rfc822_id": "<rfc822_id>",
  "bypassOnlineCheck": true
}
```

### Who calls it

- `cancelSendEmail()` — called when the user clicks "UNDO" on the send notification
- `bypassOnlineCheck: true` — attempts to cancel even when the network appears offline

### Undo flow

1. User clicks send → `messages/send` called with a `delay` (default 20 seconds)
2. During the delay, "Undo" notification shown
3. User clicks UNDO → `messages/send/cancel` called
4. Backend cancels the delayed send if still pending
5. Success → draft is restored; Failure → `UNDO_FAILED` state

### Confidence: High

---

## 2. `messages/send/log`

Log send-related events for analytics and debugging.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/messages/send/log` |
| **Method** | POST |

### Request body

```json
{
  "action": "draft_ready" | "cancel" | "click_send" | "postpone",
  "draft": { ... },
  "diagnostics": { ... },
  "reminder": { ... },
  "superhuman_id": "<id>",
  "draft_message_id": "<id>",
  "draft_thread_id": "<id>",
  "client_sent_at": "2026-03-23T10:00:00.000Z"
}
```

| Action | When |
|--------|------|
| `draft_ready` | Draft is finalized, about to be sent |
| `click_send` | User clicked the send button |
| `cancel` | User clicked undo |
| `postpone` | Send delay was extended |

### Who calls it

- `logSend()` — called at various points in the send lifecycle
- Also used by the "black box" recording system for debug replay

### Confidence: High

---

## 3. `messages/send/<id>/postpone`

Extend or shorten the send delay for an in-flight message.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/messages/send/<superhuman_id>/postpone` |
| **Method** | POST |

### Request body

```json
10
```

The body is just a number — the new delay in seconds:
- `10` — extend the undo window by 10 seconds (on mouse hover)
- `0` — instant send (user clicked "INSTANT SEND ⚡")

### Who calls it

- `postponeSendEmail(superhumanId, delay)` — called in two scenarios:
  1. **Mouse hover** over the sending notification → extends delay by `HOVER_SEND_DELAY` seconds (throttled to every 5s)
  2. **Instant send** (`⌘+Shift+Z`) → sets delay to 0 for immediate delivery

### Confidence: High

---

## 4. `messages.modifyLabels`

Add or remove Superhuman-specific labels on a message (team labels, shared labels).

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/messages.modifyLabels` |
| **Method** | POST |

### Request body — add labels

```json
{
  "messageId": "<message_id>",
  "labelsToAdd": ["SH_TEAM", "SH_SHARED"],
  "lastTeamMessageId": "<id>",
  "lastSharedAt": "2026-03-23T10:00:00.000Z"
}
```

### Request body — remove labels

```json
{
  "messageId": "<message_id>",
  "labelsToRemove": ["SH_TEAM"],
  "lastTeamMessageId": "<id>",
  "lastSharedAt": "2026-03-23T10:00:00.000Z"
}
```

### Who calls it

- `addSuperhumanLabels()` / `removeSuperhumanLabels()` — for team collaboration features

### Confidence: Medium

---

## Summary table

| Endpoint | Method | Purpose | Confidence |
|----------|--------|---------|------------|
| `messages/send/cancel` | POST | Undo a recently sent email | High |
| `messages/send/log` | POST | Log send lifecycle events | High |
| `messages/send/<id>/postpone` | POST | Extend/shorten send delay | High |
| `messages.modifyLabels` | POST | Add/remove Superhuman labels | Medium |
