# Superhuman Teams, MCP, & Miscellaneous Endpoints

Last updated: 2026-03-23

## Scope

Team management, billing, MCP token generation, user lifecycle, translation, and other utility endpoints.

---

## MCP token generation

### `mcp.generateToken`

Generate an MCP (Model Context Protocol) token for third-party AI integrations.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/mcp.generateToken` |
| **Method** | POST |

```json
{ "token_name": "claude-code" }
```

**Important context**: In our earlier testing (see `docs/official-superhuman-mcp-beta.md`), this endpoint returned `404`. It may be behind a feature flag or not yet deployed. The code exists in the bundle and calls it from the `generateMCPToken()` method.

### Confidence: Low (code exists but endpoint 404'd in testing)

---

## Teams & members

### `teams.classify`

Classify email addresses into groups (for referral suggestions).

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/teams.classify` |

```json
{
  "emails": ["alice@example.com", "bob@example.com"],
  "showSuperhumanUsersSeparately": true
}
```

Returns `{ "groups": [...] }`.

### `teams.suggest`

Get team suggestions (for join-a-team flows).

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/teams.suggest?includeSingletons=true` |
| **Method** | GET |

Returns `{ "teams": [...] }`.

### `teams.invite`

Invite someone to the team.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/teams.invite` |

```json
{
  "emailAddress": "alice@example.com",
  "invitedFriendlyName": "Alice Smith",
  "delay": false
}
```

### `teams.uninvite`

Revoke a team invitation.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/teams.uninvite` |

```json
{ "emailAddress": "alice@example.com" }
```

### `teams.canInvite`

Check if the user can invite specific emails.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/teams.canInvite` |

```json
{ "emails": ["alice@example.com"] }
```

### `teams.join`

Join a team.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/teams.join` |

```json
{
  "stripeCustomerId": "<stripe_id>",
  "source": "invite_link"
}
```

### `teams.members`

Get team members and invites.

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/teams.members` |

Optional query: `?allowBundleCustomer=true`

Returns `{ "members": [...], "invites": [...], "user": {...} }`.

### `teams.resetInviteLink`

Reset the team invite link.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/teams.resetInviteLink` |
| **Body** | None |

### `teams.billing`

Get team billing information.

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/teams.billing` |

### `teams.getBillingFeaturesBySku`

Get billing features by SKU.

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/teams.getBillingFeaturesBySku` |

### `teams.updateBillingTier`

Update the billing tier.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/teams.updateBillingTier` |

```json
{ "tier": "business" }
```

---

## Member approval

### `members.approve`

Approve a join request.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/members.approve` |

```json
{ "joinRequestId": "<id>" }
```

### `members.reject`

Reject a join request.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/members.reject` |

```json
{ "joinRequestId": "<id>" }
```

### `members.requestUpgrade`

Request an account upgrade (from member to admin, presumably).

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/members.requestUpgrade` |
| **Body** | `{}` |

---

## User lifecycle

### `users.active`

Mark the user as active (heartbeat).

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/users.active` |
| **Body** | `{}` |

Debounced on the client.

### `users.getReferral`

Get the user's referral link.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/users.getReferral` |
| **Body** | None |

Returns `{ "link": "https://..." }`.

### `users.refreshAliases`

Refresh email aliases from the provider.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/users.refreshAliases` |
| **Body** | None |

Debounced on the client.

### `users.achievements`

Get user achievements (for recap/year-in-review features).

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/users.achievements` |

---

## Utilities

### `translate.detectLanguage`

Detect the language of text content.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/translate.detectLanguage` |

```json
{ "content": "Bonjour, comment ça va?" }
```

### `unsubscribe`

Process an email unsubscribe action.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/unsubscribe` |
| **Auth** | ID token (direct, not via standard backend auth) |

```json
{ "url": "https://example.com/unsubscribe?token=abc" }
```

### `metrics.write`

Write frontend performance metrics.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/metrics.write` |

```json
{
  "metrics": [...],
  "client_timestamp": 1711180800000,
  "dataset": "frontend"
}
```

Used by the profiler system. Events are batched and sent periodically. Reliability events are re-queued on failure.

---

## Browser management

### `browsers.create`

Register a browser instance.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/browsers.create` |

### `browsers.delete`

Unregister a browser instance.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/browsers.delete` |

---

## CSRF

### `csrfToken.create`

Generate a CSRF token for anonymous shared link operations.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/csrfToken.create` |

Used by the shared link viewer (anonymous access, no Superhuman account needed).

---

## Summary table

| Endpoint | Method | Purpose | Confidence |
|----------|--------|---------|------------|
| `mcp.generateToken` | POST | Generate MCP token | Low (404'd) |
| `teams.classify` | POST | Classify emails for referrals | Medium |
| `teams.suggest` | GET | Get team suggestions | Medium |
| `teams.invite` | POST | Invite to team | High |
| `teams.uninvite` | POST | Revoke invitation | High |
| `teams.canInvite` | POST | Check invite eligibility | Medium |
| `teams.join` | POST | Join a team | Medium |
| `teams.members` | GET | List team members | Medium |
| `teams.resetInviteLink` | POST | Reset invite link | Medium |
| `teams.billing` | GET | Get billing info | Low |
| `teams.getBillingFeaturesBySku` | GET | Get SKU features | Low |
| `teams.updateBillingTier` | POST | Change billing tier | Low |
| `members.approve` | POST | Approve join request | High |
| `members.reject` | POST | Reject join request | High |
| `members.requestUpgrade` | POST | Request upgrade | Low |
| `users.active` | POST | Activity heartbeat | Medium |
| `users.getReferral` | POST | Get referral link | Medium |
| `users.refreshAliases` | POST | Refresh email aliases | Medium |
| `users.achievements` | GET | Get achievements | Low |
| `translate.detectLanguage` | POST | Language detection | High |
| `unsubscribe` | POST | Process unsubscribe | Medium |
| `metrics.write` | POST | Write perf metrics | Medium |
| `browsers.create` | POST | Register browser | Low |
| `browsers.delete` | POST | Unregister browser | Low |
| `csrfToken.create` | POST | Generate CSRF token | Medium |
