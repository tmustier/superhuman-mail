# Superhuman Calendar API Endpoints

Last updated: 2026-03-23

## Scope

This document covers calendar-related endpoints observed in the Superhuman web bundle (`7b67e9e565506c551126.page.js`) and test fixtures. It is a companion to `docs/superhuman-api-endpoints.md`.

None of these endpoints have been **live-validated** by this repo yet. Confidence levels reflect how much we can infer from the minified bundle.

For notes on the official Superhuman Mail MCP beta calendar tools, see:
- `docs/official-superhuman-mcp-beta.md`

## Architecture overview

Superhuman's calendar is a **dual-provider system**:

- **Google Calendar**: Superhuman proxies directly to the Google Calendar API (`googleapis.com/calendar/v3/...`) using the user's OAuth token. The `gcal` service class handles all Google Calendar operations client-side.
- **Microsoft/Outlook Calendar**: Superhuman uses a server-side proxy endpoint (`microsoftCalendar.proxy`) to forward requests to the Microsoft Graph API. The `syncOutlookCalendar` service handles sync.

On top of this, Superhuman has its own backend endpoints for:
- Calendar account authentication (`calendars.authenticate`)
- Event creation that works across both providers (`calendarEvents.create`)
- Shared availability / booking pages (`calendarAvailability.create`)
- AI-powered calendar details extraction (`ai.calendarDetails`)
- Smart send timing (`smartsend.getTimeRange`)
- Out-of-office responder settings (`outOfOfficeResponder.updateForProvider`)
- Zoom meeting management (`zoom.*`)

### Calendar data model

Calendar accounts are stored in Superhuman's userdata settings at:

```
settings/calendarAccounts/<email>
```

Each calendar account object:

```json
{
  "googleId": "<provider_user_id>",
  "email": "<calendar_account_email>",
  "authed": true,
  "calendars": {
    "<calendar_id>": {
      "show": true,
      "color": "<hex_color>"
    }
  },
  "provider": "google" | "microsoft"
}
```

Booking pages / recurring availabilities are stored at:

```
settings/calendar/recurringAvailabilities
```

```json
{
  "models": {
    "<externalId>": { "name": "...", "externalId": "...", "lastUsedTimestamp": 1234567890, ... }
  },
  "orderedIds": ["<externalId>", ...]
}
```

---

## Core calendar endpoints

### 1. `calendarEvents.create`

Creates a calendar event via Superhuman's backend.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/calendarEvents.create` |
| **Method** | POST |
| **Content-Type** | application/json |
| **Auth** | Superhuman session (id token) |

#### Request body

The payload is a **full calendar event object**, shaped differently for Google vs Microsoft:

**Google Calendar format** (pass-through to Google API):

```json
{
  "iCalUID": "<event_id>",
  "summary": "Meeting title",
  "description": "Event description",
  "start": "2026-03-25T10:00:00.000Z",
  "end": "2026-03-25T11:00:00.000Z",
  "allDay": false,
  "calendarId": "<calendar_id>",
  "calendarAccountEmail": "user@example.com",
  "attendees": [
    { "email": "attendee@example.com", "displayName": "Name" }
  ],
  "location": "Conference Room A",
  "conferenceData": { ... },
  "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO"],
  "status": "confirmed",
  "transparency": "opaque",
  "visibility": "default"
}
```

For all-day events, `start` and `end` are converted to ISO strings before sending.

**Microsoft/Outlook format** (formatted by `_formatOutlookCreateEventForBackend`):

```json
{
  "iCalUID": "<event_id>",
  "summary": "Meeting title",
  "start": "2026-03-25T10:00:00Z",
  "end": "2026-03-25T11:00:00Z",
  "allDay": false,
  "calendarId": "<calendar_id>",
  "calendarAccountEmail": "user@example.com",
  "attendees": [...],
  "timeZone": "Europe/London",
  "location": "...",
  "conferenceData": { ... }
}
```

Key differences for Microsoft:
- Dates formatted as `yyyy-MM-dd'T'HH:mm:ss'Z'`
- `timeZone` field added (from `Intl.DateTimeFormat().resolvedOptions().timeZone`)
- Conference data merged via a helper that adds Zoom/Meet link data
- `googleMeetData`, `zoomData`, `meetingProvider` fields are stripped before sending

#### Response

```json
{
  "id": "<provider_event_id>"
}
```

The returned `id` is the provider-side event ID (Google or Microsoft).

From test fixtures, a minimal success response is `{ "code": 200 }`.

#### Who calls it

- `CalendarStore.createCalendarEvent()` — main entry point from the calendar UI
- Called when the user creates an event in "See Your Week" / "See Your Day"
- Also used internally for conference data provisioning (creates a hidden placeholder event to obtain a Google Meet / Zoom link, then cancels it)

#### Confidence: Medium

The endpoint URL and method are certain. The request body shape is inferred from the formatting functions — the exact fields accepted by the backend may be a superset (the backend likely proxies to Google/Microsoft APIs).

---

### 2. `calendarAvailability.create`

Stores a shared availability selection. This is the backend persistence for the "Share Availability" compose feature (⌘+Shift+A).

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/calendarAvailability.create` |
| **Method** | POST |
| **Content-Type** | application/json |
| **Auth** | Superhuman session (id token) |

#### Request body

The payload is a `sharedAvailability` object. From the UI code we can infer it contains:

```json
{
  "slots": [
    {
      "startTime": "2026-03-25T10:00:00.000Z",
      "endTime": "2026-03-25T11:00:00.000Z"
    }
  ],
  "mode": "link" | "plainText",
  "title": "30 Min Meeting",
  "conference": {
    "provider": "zoom" | "google_meet"
  },
  "where": "Conference Room A"
}
```

Profiler telemetry captured alongside calls includes:
- `include_booking_link`: whether mode is `"link"`
- `has_meet_link`, `has_title`, `has_location`
- `slots[].start_time`, `slots[].end_time`, `slots[].duration`

#### Response

Standard JSON response (shape not fully observed from the bundle).

#### Who calls it

- `SuperhumanBackend.storeCalendarSharedAvailability(sharedAvailability)` — called from the `createSharedAvailability` modifier
- Triggered when user inserts availability from the compose autocomplete (⌘+Shift+A)
- Two availability types in the UI:
  - **One-time**: "Insert free times" or "Insert free times and booking link"
  - **Recurring (Booking Pages)**: stored in `settings/calendar/recurringAvailabilities` and persisted via `settings.set()`, not through this endpoint

#### Error handling

Catches fetch errors and throws a `DoomedModifier` on network failures:
```
Calendar Create Shared Availability Error: FetchError {status}, {body}
```

#### Confidence: Medium

The endpoint and method are certain. The request body shape is partially inferred from the UI that constructs it — the `slots` array structure is confirmed from profiler instrumentation, but there may be additional fields.

---

### 3. `calendars.authenticate`

Authenticates a calendar account (Google or Microsoft) and links it to the Superhuman user.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/calendars.authenticate` |
| **Method** | POST |
| **Content-Type** | application/json |
| **Auth** | Superhuman session (id token + email) |

#### Request body

**Google provider:**

```json
{
  "code": "<google_oauth_authorization_code>",
  "scopes": "<requested_scopes>",
  "provider": "google",
  "samlState": "<saml_state_if_applicable>"
}
```

**Microsoft provider:**

```json
{
  "assertion": "<microsoft_access_token>",
  "scopes": "<requested_scopes>",
  "provider": "microsoft",
  "samlState": "<saml_state_if_applicable>"
}
```

#### Response

```json
{
  "emailAddress": "<calendar_account_email>",
  "providerId": "<provider_user_id>"
}
```

Both fields are required — the code throws if either is missing.

#### Post-authentication flow

After successful auth:
1. Adds the calendar account to userdata settings at `settings/calendarAccounts/<email>`
2. For Microsoft: triggers a full Outlook calendar sync (`syncOutlookCalendar.syncAll()`)
3. For Google: syncs the calendar list, enables primary calendars by default
4. Creates calendar watchers for the new account

#### Test fixture behavior

From the mock server in the bundle:
```javascript
// Google: extracts user ID from code ("code:userId")
// Microsoft: extracts user ID from assertion ("assertion:userId")
// Writes to mockUserData:
{
  path: "settings/calendarAccounts/<userId>",
  value: {
    googleId: "1234",
    email: "<email>",
    authed: true,
    calendars: {},
    provider: "google" | "microsoft"
  }
}
```

#### Confidence: High

Both the request and response shapes are visible from the production code and test fixtures.

---

### 4. `microsoftCalendar.proxy`

Server-side proxy for Microsoft Graph Calendar API calls. Superhuman routes all Outlook calendar operations through this proxy (unlike Google Calendar, which is called directly from the client).

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/microsoftCalendar.proxy` |
| **Method** | POST |
| **Content-Type** | application/json |
| **Auth** | Superhuman session (id token) |

#### Request body

```json
{
  "calendar_account_email": "user@outlook.com",
  "url": "https://graph.microsoft.com/v1.0/me/calendar/events",
  "method": "GET",
  "headers": { ... },
  "body": "{ ... }"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `calendar_account_email` | string | The Outlook account email to act on behalf of |
| `url` | string | The full Microsoft Graph API URL to proxy to |
| `method` | string | HTTP method (`GET`, `POST`, `PATCH`, `DELETE`) |
| `headers` | object | Optional headers (the `Authorization` header is **stripped** by the client before sending — the backend adds its own) |
| `body` | string | Optional request body (JSON string) |

#### Response

The proxied response from the Microsoft Graph API, passed through as-is.

#### Auth check

The mock server validates that the `calendar_account_email` is present in the user's `calendarAccounts` settings and has `authed: true`. If not, returns 403.

#### Who calls it

Used by the Outlook calendar sync service for all Microsoft calendar operations:
- Listing calendars
- Getting events
- Creating/updating/deleting events
- Querying free/busy times

#### Confidence: High

Both the request shape and proxy behavior are fully visible in the production code and mock server.

---

### 5. `ai.calendarDetails`

AI-powered extraction of calendar-relevant details from an email thread. Used to pre-populate event creation fields.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/ai.calendarDetails` |
| **Method** | POST |
| **Content-Type** | application/json |
| **Auth** | Superhuman session (id token) |
| **Response type** | **Streaming** (SSE / ReadableStream) |

#### Request body

```json
{
  "thread_content": "<full thread text content>",
  "local_datetime": "2026-03-23T10:00:00+00:00",
  "stream_response": true,
  "selected_date": "2026-03-25"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `thread_content` | string | Yes | The email thread content to analyze |
| `local_datetime` | string | Yes | User's current local datetime (moment.js formatted) |
| `stream_response` | boolean | Yes | Always `true` in observed usage |
| `selected_date` | string | No | Optional date constraint (e.g., from calendar day click) |

#### Response

Streaming response read via `ReadableStream.getReader()`. The streamed content likely contains extracted event details such as:
- Suggested event title / summary
- Proposed dates/times
- Attendees
- Location

The exact streamed response schema is not visible in the minified bundle — the stream is consumed by `_streamResponse()` and rendered progressively.

#### Who calls it

- `SuperhumanBackend.getAICalendarDetails(threadContent, selectedDate)` — returns a stream reader function
- Triggered when user creates an event from an email thread and Superhuman AI pre-fills details

#### Confidence: Medium

The request shape is certain. The streaming response format is not fully observable from the bundle.

---

## Calendar-adjacent endpoints

### 6. `smartsend.getTimeRange`

Gets optimal send time for recipients based on their calendar/timezone data.

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/smartsend.getTimeRange?emails=a@x.com,b@y.com` |
| **Method** | GET |
| **Auth** | Superhuman session (id token) |

#### Query parameters

| Param | Type | Description |
|-------|------|-------------|
| `emails` | string | Comma-separated list of recipient emails |

#### Response

Returns time range data for smart send scheduling. On error, returns:

```json
{
  "code": 500,
  "detail": "Internal Server Error",
  "error": "backend-fetch-error"
}
```

#### Who calls it

- `SuperhumanBackend.getSmartSendData(emails[])` — returns `null` if email list is empty

#### Confidence: Medium

---

### 7. `autoDrafts.previewEAScheduling`

Previews EA (Executive Assistant) auto-draft scheduling suggestions.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/autoDrafts.previewEAScheduling` |
| **Method** | POST |
| **Content-Type** | application/json |
| **Auth** | Superhuman session (id token) |

#### Request body

Not fully visible from the bundle — the payload is passed through from the caller.

#### Who calls it

- `SuperhumanBackend.getAutoDraftEASchedulingPreview(payload)` — used by the auto-draft scheduling preview feature
- Related to Superhuman's AI auto-drafting that can suggest meeting times

#### Confidence: Low

---

### 8. `outOfOfficeResponder.updateForProvider`

Updates out-of-office / automatic reply settings on the email provider.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/outOfOfficeResponder.updateForProvider` |
| **Method** | POST |
| **Content-Type** | application/json |
| **Auth** | Superhuman session (id token) |

#### Request body

Provider-specific OOO settings. For Google:

```json
{
  "enableAutoReply": true,
  "responseBodyHtml": "<p>I'm out of office...</p>",
  "restrictToContacts": false,
  "restrictToDomain": false,
  "startTime": 1711324800000,
  "endTime": 1711411200000
}
```

For Microsoft, the settings are read from `automaticRepliesSetting`:

```json
{
  "externalAudience": "all" | "contactsOnly" | "none",
  "externalReplyMessage": "...",
  "internalReplyMessage": "...",
  "status": "alwaysEnabled" | "scheduled" | "disabled",
  "scheduledStartDateTime": { "dateTime": "..." },
  "scheduledEndDateTime": { "dateTime": "..." }
}
```

#### Superhuman-side settings

OOO state is also stored locally in Superhuman settings at `outOfOfficeResponder`:

```json
{
  "startTime": 1711324800000,
  "endTime": 1711411200000,
  "google": {
    "enabledAutoReplyAt": "...",
    "responseBodyHtml": "..."
  },
  "outlook": {
    "status": "alwaysEnabled" | "scheduled" | "disabled"
  }
}
```

The `isOutOfOffice()` method checks these settings to determine if the user is currently OOO.

#### Confidence: Medium

---

### 9. Zoom endpoints

Four endpoints for Zoom meeting management.

#### `zoom.authenticate`

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/zoom.authenticate` |
| **Method** | POST |

```json
{ "authorization_code": "<zoom_oauth_code>" }
```

Authenticates the user's Zoom account via OAuth. The authorization code comes from an OAuth popup (`emailAddress` is passed for context).

#### `zoom.createMeeting`

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/zoom.createMeeting` |
| **Method** | POST |

```json
{ "type": 3 }
```

Where `type` corresponds to Zoom meeting types. Observed value: `RECURRING_NO_FIXED_TIME` (used to create a placeholder meeting for reusable Zoom links on events).

#### `zoom.updateMeeting`

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/zoom.updateMeeting?id=<meeting_id>` |
| **Method** | POST |

Body: meeting data object (fields not fully visible).

#### `zoom.deleteMeeting`

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/zoom.deleteMeeting?id=<meeting_id>` |
| **Method** | POST |

No body.

#### `zoom.signOut`

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/zoom.signOut` |
| **Method** | POST |

No body.

#### Confidence: Medium

---

## Google Calendar API (client-side, not Superhuman endpoints)

Superhuman's `gcal` service talks **directly** to the Google Calendar API. These are not Superhuman backend endpoints, but they're important context for understanding the calendar architecture.

The client makes authenticated requests to `googleapis.com` using the user's Google OAuth token (managed by the Superhuman auth system).

| Operation | Method | URL |
|-----------|--------|-----|
| List events | GET | `googleapis.com/calendar/v3/calendars/{calendarId}/events` |
| Get event | GET | `googleapis.com/calendar/v3/calendars/{calendarId}/events/{eventId}` |
| Get recurring instances | GET | `googleapis.com/calendar/v3/calendars/{calendarId}/events/{eventId}/instances` |
| Import event | POST | `googleapis.com/calendar/v3/calendars/{calendarId}/events/import` |
| Update event | PUT | `googleapis.com/calendar/v3/calendars/{calendarId}/events/{eventId}` |
| Patch event | PATCH | `googleapis.com/calendar/v3/calendars/{calendarId}/events/{eventId}?conferenceDataVersion=1&sendUpdates=all` |
| Delete event | DELETE | `googleapis.com/calendar/v3/calendars/{calendarId}/events/{eventId}?sendUpdates=all` |
| Free/busy query | POST | `googleapis.com/calendar/v3/freeBusy` |
| List calendar list | GET | `googleapis.com/calendar/v3/users/me/calendarList` |

All calls include `calendarAccountEmail` as a parameter, used for credential selection.

The `getEventsList` call includes `eventTypes: ["default", "focusTime", "outOfOffice", "fromGmail"]` by default.

---

## Cross-reference with official MCP beta

The official Superhuman Mail MCP beta (see `docs/official-superhuman-mcp-beta.md`) exposes these calendar-related tools:

| MCP tool | Likely maps to |
|----------|----------------|
| `create_or_update_event` | `calendarEvents.create` + Google/Microsoft update APIs |
| `get_availability_calendar` | `gcal.freebusy.query` / `gcal.events.list` |
| `query_email_and_calendar` | Combined email search + calendar event search |
| `update_preferences_email_and_calendar` | Settings writes to `calendarAccounts` |

The MCP provides a higher-level abstraction over these endpoints. For direct API access, the endpoints documented here are the underlying primitives.

---

## Summary table

| Endpoint | Method | Validated | Confidence | Purpose |
|----------|--------|-----------|------------|---------|
| `calendarEvents.create` | POST | ❌ | Medium | Create calendar events (Google + Microsoft) |
| `calendarAvailability.create` | POST | ❌ | Medium | Store shared availability from compose |
| `calendars.authenticate` | POST | ❌ | High | Auth Google/Microsoft calendar accounts |
| `microsoftCalendar.proxy` | POST | ❌ | High | Proxy to Microsoft Graph Calendar API |
| `ai.calendarDetails` | POST | ❌ | Medium | AI extraction of event details from threads |
| `smartsend.getTimeRange` | GET | ❌ | Medium | Optimal send time for recipients |
| `autoDrafts.previewEAScheduling` | POST | ❌ | Low | Auto-draft scheduling preview |
| `outOfOfficeResponder.updateForProvider` | POST | ❌ | Medium | Update OOO settings on provider |
| `zoom.authenticate` | POST | ❌ | Medium | Zoom OAuth |
| `zoom.createMeeting` | POST | ❌ | Medium | Create Zoom meeting |
| `zoom.updateMeeting` | POST | ❌ | Medium | Update Zoom meeting |
| `zoom.deleteMeeting` | POST | ❌ | Medium | Delete Zoom meeting |
| `zoom.signOut` | POST | ❌ | Medium | Zoom sign-out |

## Suggested next steps

1. **Validate `calendarEvents.create`** — create a test event on a secondary calendar, then verify it appears in Google Calendar
2. **Validate `calendars.authenticate`** — attempt to add a secondary Google calendar account
3. **Validate `ai.calendarDetails`** — send a thread with meeting details and capture the streaming response format
4. **Explore `calendarAvailability.create`** — share availability from compose and capture the request payload via network inspection
5. **Document the availability slot format** — the "Insert free times" and "Booking Pages" features construct slot objects that warrant detailed field documentation
