# Superhuman Read Statuses / Read Receipts

Last updated: 2026-03-23

## Short answer

Yes — Superhuman's private API exposes read-status data.

The clearest confirmed surface is:

- `POST /~backend/v3/userdata.read`
  - reading path: `users/<googleId>/threads/<threadId>`

That response includes per-message read-status metadata under:

- `messages.<messageId>.reads`
- `messages.<messageId>.readsSharedBy`

This is the same data that powers:

- the thread-level read-status UI
- the **Recent Opens** activity feed
- the official MCP beta tool `get_read_statuses`

---

## Live validation

On 2026-03-23 we live-validated this against a real existing thread using:

```bash
shm opens <thread_id>
```

and also by calling `POST /~backend/v3/userdata.read` directly.

The backend returned a payload shaped like:

```json
{
  "results": [
    {
      "path": "users/<googleId>/threads/<threadId>",
      "value": {
        "historyId": 23730,
        "containerUpdatedAt": "2026-03-23T10:44:50.268108919Z",
        "messages": {
          "<messageId>": {
            "reads": {
              "recipient@example.com": [
                {
                  "device": "desktop",
                  "readAt": "2026-03-11T23:17:01.000000000Z"
                }
              ],
              "teammate@example.com": [
                {
                  "device": "mobile",
                  "readAt": "2026-03-12T10:22:19.000000000Z"
                }
              ]
            },
            "readsSharedBy": "sender@example.com",
            "mentions": null,
            "historyId": 17109
          }
        }
      }
    }
  ]
}
```

We also checked the local Superhuman SQLite cache and found historical threads with the same fields persisted in `threads.superhuman_data`.

---

## Data model

### Per-message read data

Read status lives in the **Superhuman data** for each message, not in the provider message JSON.

```json
{
  "messages": {
    "<messageId>": {
      "reads": {
        "recipient@example.com": [
          {
            "device": "desktop",
            "readAt": "2026-03-11T23:17:01.000000000Z"
          },
          {
            "device": "mobile",
            "readAt": "2026-03-12T08:14:22.000000000Z"
          }
        ]
      },
      "readsSharedBy": "thomas@nexcade.ai"
    }
  }
}
```

### Field meanings

| Field | Type | Meaning |
|---|---|---|
| `reads` | object | Map of email → array of read events |
| `reads.<email>[]` | array | Multiple opens/reads per recipient |
| `device` | string | Observed values: `desktop`, `mobile` |
| `readAt` | ISO timestamp | When the read/open was observed |
| `readsSharedBy` | string | Which teammate shared the read status |

---

## Where the data comes from

### 1. Single-thread fetch: `userdata.read`

This is the cleanest confirmed private API path.

Bundle trace:

- `getThread(r) { return this.readUserData(\`threads/${r}\`) }`
- `readUserData(...)` calls:
  - `POST /~backend/v3/userdata.read`

Then the sync service uses it here:

- `SyncThread.updateFromBackendAsync(...)`
  - `const y = await this._backend.getThread(threadId)`
  - `await this.saveSuperhumanDataAsync(..., y, ...)`

So for a single thread, read-status data is fetched through:

1. `backend.getThread(threadId)`
2. `POST /~backend/v3/userdata.read`
3. `saveThreadSuperhumanDataAsync(threadId, superhumanData)`
4. persisted locally as `threads.superhuman_data`

### 2. Thread list fetches: `userdata.getThreads`

Bundle trace:

- `POST /~backend/v3/userdata.getThreads`
- `_processGetThreadsResult(r)`
- for each result: `_backendToAppThread(y.thread)`

This means the list-fetch path can also deliver thread-level Superhuman metadata, including read-status fields.

### 3. Incremental sync: `userdata.sync`

Bundle trace:

- `POST /~backend/v3/userdata.sync`
- `_backendToAppForSync(r)`

Important detail:

- `_backendToAppForSync()` normalizes known draft/send-job/attachment fields
- it does **not** strip `reads` or `readsSharedBy`

So if those fields are present in sync payloads, they pass through and can be persisted.

---

## How it is persisted locally

The thread cache stores two blobs:

- `threads.json` — provider thread JSON
- `threads.superhuman_data` — Superhuman-specific metadata

That is why older threads in the local DB still retain historical `reads` / `readsSharedBy` data.

---

## Recent Opens feed

Read statuses also power Superhuman's **Recent Opens** feature.

### Local table

The local SQLite schema includes:

```sql
CREATE TABLE activity_feed (
  id INTEGER PRIMARY KEY,
  email TEXT,
  thread_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  updated_at INTEGER NOT NULL
)
```

Observed rows look like:

```text
(email, thread_id, message_id, updated_at)
```

### Practical interpretation

The Recent Opens feed appears to be **derived locally from thread read-status data**, not fetched from a dedicated standalone endpoint.

In this repo, the CLI exposes that local cache via:

```bash
shm opens --recent
shm opens --recent --recipient someone@example.com
```

---

## What we did **not** find

We did **not** find a clean, dedicated private endpoint like:

- `readStatuses.get`
- `readReceipts.list`
- `recentOpens.get`

Current best interpretation:

- the private web app gets read-statuses as part of **thread Superhuman data**
- the Recent Opens feed is then **derived and cached locally**

So the effective private API surface is lower-level than the official MCP beta's `get_read_statuses` abstraction.

---

## Practical takeaway

If you want read receipts from the reverse-engineered private API today, the best path is:

1. fetch thread Superhuman data
   - `POST /~backend/v3/userdata.read`
   - path: `users/<googleId>/threads/<threadId>`
2. inspect:
   - `messages.<messageId>.reads`
   - `messages.<messageId>.readsSharedBy`

CLI helpers:

```bash
shm opens <thread_id>
shm opens <thread_id> --recipient recipient@example.com
shm opens --recent --limit 10
```

This is live-validated.

---

## Confidence

| Claim | Confidence |
|---|---|
| Read-status data exists in the private API | High |
| `userdata.read` returns `reads` / `readsSharedBy` | High |
| Local `threads.superhuman_data` persists read-statuses | High |
| Recent Opens is derived from that data locally | High |
| There is a separate standalone private read-status endpoint | Low / not found |
