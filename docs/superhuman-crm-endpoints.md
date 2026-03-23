# Superhuman CRM Integration Endpoints

Last updated: 2026-03-23

## Scope

Endpoints for HubSpot, Salesforce, and Pipedrive CRM integrations. These power the contact sidebar panels in Superhuman.

---

## HubSpot

### `crm.fields/hubspot`

Get HubSpot field definitions.

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/crm.fields/hubspot` |
| **Method** | GET |

Returns field definitions for contacts, companies, and deals. Debounced on the client.

### `crm.fields/hubspot/<objectType>/<field>/options/search`

Search picklist options for a HubSpot field.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/crm.fields/hubspot/<objectType>/<field>/options/search` |

Body: search filter object.

### `crm/hubspot/contact`

Create a HubSpot contact.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/crm/hubspot/contact` |

```json
{
  "email": "alice@example.com",
  "properties": {
    "firstname": "Alice",
    "lastname": "Smith"
  },
  "associations": {
    "companies": [{ "id": "<company_id>" }],
    "deals": [{ "id": "<deal_id>" }]
  }
}
```

### `crm/hubspot/company/search`

Search HubSpot companies.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/crm/hubspot/company/search` |

```json
{
  "filters": [{ "propertyId": "name", "value": "Acme" }]
}
```

Debounced. Requires minimum 2 characters.

### `crm/hubspot/deal/search`

Search HubSpot deals.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/crm/hubspot/deal/search` |

```json
{
  "filters": [{ "propertyId": "dealname", "value": "Enterprise" }]
}
```

Debounced. Requires minimum 2 characters.

### `crm/hubspot/<objectType>/batch/update`

Batch update HubSpot records.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/crm/hubspot/<objectType>/batch/update` |

Body: batch update payload.

### `crm/hubspot/automation/sequences`

List HubSpot sequences.

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/crm/hubspot/automation/sequences` |
| **Method** | GET |

### `crm/hubspot/automation/sequences.enroll`

Enroll a contact in a HubSpot sequence.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/crm/hubspot/automation/sequences.enroll` |

```json
{
  "email": "alice@example.com",
  "sequenceId": "<sequence_id>"
}
```

### HubSpot error handling

Returns `invalid-credentials` error when the OAuth token has expired. The client shows a modal prompting re-authentication. Errors include:
- `missingScope` — additional OAuth scopes needed
- `invalid-credentials` — token expired

---

## Salesforce

### `salesforce.fields`

Get Salesforce field definitions.

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/salesforce.fields` |
| **Method** | GET |

Returns `FORCE_SIGNOUT` in the body when permissions are revoked.

### `salesforce.createContact`

Create a Salesforce contact.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/salesforce.createContact` |

```json
{
  "fields": {
    "FirstName": "Alice",
    "LastName": "Smith",
    "Email": "alice@example.com"
  }
}
```

### `salesforce.updateProfile`

Update a Salesforce record.

| Field | Value |
|-------|-------|
| **URL** | `POST /~backend/v3/salesforce.updateProfile` |

Body: update payload.

### `salesforce.dereferences`

Dereference Salesforce entity IDs to display names.

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/salesforce.dereferences?entities=<id1>,<id2>` |
| **Method** | GET |

### `salesforce.suggestions`

Get Salesforce record suggestions.

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/salesforce.suggestions?q=<query>&sObject=<type>` |
| **Method** | GET |

---

## Pipedrive

### `crm.fields/pipedrive`

Get Pipedrive field definitions.

| Field | Value |
|-------|-------|
| **URL** | `GET /~backend/v3/crm.fields/pipedrive` |
| **Method** | GET |

Debounced on the client.

---

## Summary table

| Endpoint | Method | CRM | Purpose | Confidence |
|----------|--------|-----|---------|------------|
| `crm.fields/hubspot` | GET | HubSpot | Get field definitions | Medium |
| `crm.fields/hubspot/.../options/search` | POST | HubSpot | Search picklist options | Low |
| `crm/hubspot/contact` | POST | HubSpot | Create contact | Medium |
| `crm/hubspot/company/search` | POST | HubSpot | Search companies | Medium |
| `crm/hubspot/deal/search` | POST | HubSpot | Search deals | Medium |
| `crm/hubspot/.../batch/update` | POST | HubSpot | Batch update records | Low |
| `crm/hubspot/automation/sequences` | GET | HubSpot | List sequences | Medium |
| `crm/hubspot/automation/sequences.enroll` | POST | HubSpot | Enroll in sequence | Medium |
| `salesforce.fields` | GET | Salesforce | Get field definitions | Medium |
| `salesforce.createContact` | POST | Salesforce | Create contact | Medium |
| `salesforce.updateProfile` | POST | Salesforce | Update record | Low |
| `salesforce.dereferences` | GET | Salesforce | Dereference entity IDs | Medium |
| `salesforce.suggestions` | GET | Salesforce | Record suggestions | Medium |
| `crm.fields/pipedrive` | GET | Pipedrive | Get field definitions | Low |
