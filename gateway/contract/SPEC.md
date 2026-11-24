# Gateway API specification (v0.1)

This is the contract the workstreams build against: the gateway service
(`gateway/api`), the supervisor portal (`gateway/portal`) and the AequorOS
connector. Response shapes are written as TypeScript types; field names are exact.
The OpenAPI documents in this folder are exported from the service and must agree
with this file; where they disagree, fix the code or propose a change here.

## Conventions

- JSON, `snake_case`. Timestamps RFC 3339 UTC (`2026-11-21T15:57:12.418Z`); dates
  `YYYY-MM-DD`. Figures in a filing are exact decimal strings in the unit their section
  declares (Part 2); counts elsewhere in the API are integers.
  Percentages (`on_time_12m`, `api_availability_30d`, …) are numbers from 0 to 100,
  rounded to one decimal place.
- Identifiers: submissions are addressed by `receipt_id` (`SRG-YY-NNNNNNN`, from a
  gapless sequence, e.g. `SRG-26-0114822`); queries by `number` (`QRY-YYYY-NNNN`);
  operator sessions by `number` (`SUP-YYYY-NNNN`); institutions by BoG
  `institution_code` (4 digits, e.g. `0142`). Internal UUIDs may appear as `id`.
- Lists: `{ items: T[]; next_cursor: string | null }`, query `?limit=` (default 50,
  max 200) and `?cursor=`.
- Errors: RFC 9457 `application/problem+json`:
  `{ type: string; title: string; status: number; code: ErrorCode; detail?: string; instance?: string; issues?: Issue[] }`
  where `type` is `<base>/errors/<code>`.
- JCS = RFC 8785 JSON Canonicalization Scheme. `sha256(x)` is lowercase hex.

## Part 1 — Institution API (`/v1`, public, versioned)

`/v1` never changes incompatibly. Breaking changes ship only as `/v2`, announced at
least 12 months ahead, both served in parallel. Rate limit: 600 requests per minute
per client (`429` + `Retry-After`).

### Authentication

Every `/v1` call except the JWKS needs the client certificate **and**
`Authorization: Bearer <access_token>` bound to that certificate.

`POST /oauth/token` (`application/x-www-form-urlencoded`, client certificate required)

```
grant_type=client_credentials
client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
client_assertion=<JWT, ES256, iss=sub=client_id, aud=<token endpoint URL>, jti, iat, exp ≤ iat+300>
scope=<space-separated subset of the client's scopes>   (optional)
```

Response: `{ access_token: string; token_type: "Bearer"; expires_in: 600; scope: string }`.
The token is an ES256 JWT signed with the gateway receipt key: claims `iss`, `sub`
(client_id), `institution_code`, `scope`, `cnf: { "x5t#S256": string }`, `iat`, `exp`, `jti`.

`GET /v1/.well-known/jwks.json` — public: the gateway's receipt-key public JWKs
(current and retired-but-valid).

### Return definitions

```ts
type ReturnSummary = {
  code: string;                    // "BSD2"
  name: string;                    // "Statement of Assets and Liabilities"
  cadence: "daily" | "weekly" | "monthly" | "quarterly" | "semiannual" | "annual";
  current_version: string;         // "2026.1"
  applies_to: string[];            // institution types, e.g. ["universal_bank", "savings_and_loans"]
};
type ReturnVersion = {
  version: string;
  status: "draft" | "published" | "retired";
  first_reporting_date: string;    // the version applies from this reporting date on
  last_reporting_date: string | null;
  schema_sha256: string;
  rule_count: number;
  published_at: string | null;
};
type ReturnDetail = ReturnSummary & { versions: ReturnVersion[] };
```

- `GET /v1/returns` → `{ items: ReturnSummary[] }` (published only)
- `GET /v1/returns/{code}` → `ReturnDetail` (published and retired versions)
- `GET /v1/returns/{code}/versions/{version}/schema` → `{ schema: JSONSchema2020_12; rules: Rule[] }`

### Obligations

```ts
type Obligation = {
  return_code: string;
  return_version: string;
  reporting_date: string;
  basis: "solo" | "consolidated";
  due_at: string;
  status: "not_due" | "due" | "filed" | "filed_late" | "overdue";
  receipt_id: string | null;       // the current version, when filed
};
```

- `GET /v1/obligations?from=YYYY-MM-DD&to=YYYY-MM-DD` → `{ items: Obligation[] }` (the calling institution only)

### Filing

`POST /v1/submissions` — header `Idempotency-Key: <uuid>` required.

```ts
type FilingRequest = {
  manifest: Manifest;
  payload: Record<string, unknown>;      // must validate against the version's JSON Schema
  declaration: Declaration;
  signature: string;                     // compact JWS, see below
  documents?: DocumentUpload[];          // exactly the files manifest.documents lists
};
type DocumentUpload = { name: string; media_type: string; content_base64: string };
type Manifest = {
  institution_code: string;
  return_code: string;
  return_version: string;
  reporting_date: string;
  basis: "solo" | "consolidated";
  payload_sha256: string;                // sha256(JCS(payload))
  declaration_sha256: string;            // sha256(JCS(declaration))
  supersedes_receipt_id: string | null;  // null for a first version
  created_at: string;
  documents?: DocumentRef[];             // the files filed with the return; signed here
};
// The return as the institution's officers approved it: the Excel (with the template's
// formulas where it has them), CSV and PDF renditions. At most 8 files of up to 25 MB;
// media types: .xlsx (application/vnd.openxmlformats-officedocument.spreadsheetml.sheet),
// .csv (text/csv), .zip (application/zip: a multi-sheet return's CSVs, one per sheet),
// .pdf (application/pdf). Each upload must match its entry's sha256 and
// bytes exactly, so the manifest signature covers the documents as well as the payload.
type DocumentRef = { name: string; media_type: string; sha256: string; bytes: number };
type Declaration = {
  prepared_by_role: string;              // "Head of Financial Reporting"
  approved_by_role: string;              // "Chief Finance Officer"
  approved_at: string;
  amendment_reason: string | null;       // required when supersedes_receipt_id is set
  software: { name: string; version: string } | null;   // declared, never trusted for anything
};
```

**Signature.** Compact JWS, protected header
`{ "alg": "ES256", "kid": "<registered signing key id>", "typ": "srg-manifest+jws" }`,
payload = base64url(JCS(manifest)). ES256 signatures are raw `R || S` (64 bytes).

The gateway, in this order: authenticates the client; checks `Idempotency-Key`
(same key and identical `manifest`, `payload` and `declaration` → the original
response; same key and different content → `409 idempotency_conflict`; `signature` is
not compared, because ES256 signatures are randomised and a retry re-signs); verifies the JWS with the institution's active
signing key `kid`; checks the decoded JWS payload equals JCS(request `manifest`);
checks `manifest.institution_code` is the token's; recomputes both digests; checks
the return version is published and applies to the reporting date; validates the
payload against the JSON Schema. Any failure so far is refused with no receipt
(4xx). A well-formed filing is then **always receipted**; blocking rule failures
reject it after receipt so the evidence is kept.

A first version for an (institution, return, reporting date, basis) that already
has a current version → `409 duplicate_submission` (use an amendment). Amendments
go to `POST /v1/submissions/{receipt_id}/amendments` with the same body;
`manifest.supersedes_receipt_id` must equal the path id and be the current
version, and `declaration.amendment_reason` must be set.

Response `202`:

```ts
type Receipt = {
  receipt_id: string;
  submission_id: string;
  institution_code: string;
  return_code: string;
  return_version: string;
  reporting_date: string;
  basis: "solo" | "consolidated";
  version: number;                       // 1, 2, …
  supersedes_receipt_id: string | null;
  payload_sha256: string;
  manifest_sha256: string;               // sha256(JCS(manifest))
  signing_kid: string;
  received_at: string;
  ledger_seq: number;                    // audit-chain entry recording the receipt
};
type ReceiptEnvelope = {
  receipt: Receipt;
  receipt_jws: string;                   // compact JWS by the gateway receipt key, payload = JCS(receipt), typ "srg-receipt+jws"
  status: SubmissionState;
};
type SubmissionState = "received" | "validating" | "rejected" | "in_review" | "accepted" | "queried" | "superseded";
```

- `GET /v1/submissions?reporting_date=&return_code=` → `{ items: SubmissionStatus[]; next_cursor }`
- `GET /v1/submissions/{receipt_id}` → `SubmissionStatus`
- `GET /v1/submissions/{receipt_id}/receipt` → `ReceiptEnvelope`

```ts
type Issue = { rule_id: string; severity: "error" | "warning"; pointer: string | null; message: string };
type ValidationSummary = { rule_set: string; passed: number; failed: number; warnings: number; issues: Issue[] };
type SubmissionStatus = {
  receipt_id: string; return_code: string; return_version: string; reporting_date: string;
  basis: "solo" | "consolidated"; version: number; status: SubmissionState; received_at: string;
  validation: ValidationSummary | null; decided_at: string | null; superseded_by: string | null;
};
```

### Queries (supervisor questions to the institution)

```ts
type QueryMessage = { id: string; author: "supervisor" | "institution" | "system"; author_label: string; body: string; created_at: string };
type InstitutionQuery = {
  number: string; kind: "query" | "resubmission"; subject: string;
  status: "awaiting_institution" | "awaiting_supervisor" | "closed";
  receipt_id: string; due_at: string | null; opened_at: string; acknowledged_at: string | null;
  messages: QueryMessage[];
};
```

- `GET /v1/queries?status=open|all` → `{ items: InstitutionQuery[]; next_cursor }`
- `POST /v1/queries/{number}/acknowledgements` → `204` (records delivery to the institution's system)
- `POST /v1/queries/{number}/responses` `{ body: string; resubmission_receipt_id: string | null }` → `201 QueryMessage`

### Error codes

`invalid_request`, `unauthenticated`, `certificate_required`, `certificate_mismatch`,
`invalid_client_assertion`, `insufficient_scope`, `idempotency_key_required`,
`idempotency_conflict`, `signing_key_unknown`, `signing_key_revoked`,
`signature_invalid`, `manifest_mismatch`, `payload_digest_mismatch`,
`declaration_digest_mismatch`, `document_invalid`, `document_digest_mismatch`,
`institution_mismatch`, `return_unknown`,
`return_version_not_applicable`, `schema_invalid` (with `issues`),
`duplicate_submission`, `supersedes_not_current`, `amendment_reason_required`,
`not_found`, `forbidden`, `rate_limited`, `conflict`.

## Part 2 — Return content and rules

Every return uses one payload shape. Figures are exact decimal strings
(`^-?(0|[1-9][0-9]*)(\.[0-9]+)?$`) in the unit their section declares, or `null` for a
blank cell; a line a definition marks as text carries free text.

```json
{
  "currency": "GHS",
  "lines": { "BSD2.R7.domestic": "412.35", "BSD2!D7": "500.1", "cet1:1.1": "35172763.04" },
  "labels": { "cet1:1.1": "Retained earnings" }
}
```

A definition's schema (`GET /v1/returns/{code}/versions/{version}/schema`) has two kinds
of section, listed in `x-srg-sections` (`code`, `title`, `unit`, `itemised`):

- **Fixed lines**, each a required property of `lines` and listed in `x-srg-lines`
  (`code`, `label`, `total`, `section`, `kind`). For the official BoG forms these are the
  workbook's input cells and its plain sum-or-difference formulas (coded `<sheet>!<cell>`).
- **Itemised sections**, whose rows come from the institution's own data: lines coded
  `<section>:<row>` (other columns `<section>:<row>:<column>`), each row's label in
  `labels`. The schema's `patternProperties` say which keys a section accepts.

Units: `millions` and `thousands` (of the currency), `units` (the currency itself, or a
plain number), `percent`, `count`, `text`.

Rules are data, evaluated by a small safe engine (never `eval`), in exact decimal
arithmetic; a blank cell counts as zero:

```ts
type Rule =
  | { id: string; severity: "error" | "warning"; kind: "sum"; target: string; terms: string[]; subtract?: string[]; tolerance: number | string; description: string }
  | { id: string; severity: "error" | "warning"; kind: "non_negative"; lines: string[]; description: string }
  | { id: string; severity: "error" | "warning"; kind: "compare"; left: string; op: ">=" | "<=" | "=="; right: string; description: string }
  | { id: string; severity: "error" | "warning"; kind: "cross_equal"; line: string; other_return: string; other_line: string; tolerance: number | string; description: string }
  | { id: string; severity: "warning"; kind: "variance"; line: string; max_change_pct: number; description: string }
  | { id: string; severity: "error" | "warning"; kind: "present"; section: string; description: string };
```

`sum` checks `target = Σ terms − Σ subtract` within `tolerance` (the official forms'
formulas allow 0.005 of the unit per figure for rounding). `present` requires at least one
row in an itemised section. `cross_equal` compares against the institution's current
non-rejected filing of `other_return` for the same reporting date and basis; if none
exists it yields a warning "not checked". `variance` compares against the previous
reporting date's current filing. Rule groups for display come from the rule id prefix
(`BSD2-STR-*` structure, `-ARI-*` arithmetic, `-XR-*` cross-return, `-PER-*` period).

In `local|test|sandbox` the definitions are the AequorOS return templates
(`api/app/returns/catalogue/`, exported by `backend/scripts/export_gateway_catalog.py`
and published with `python -m app.returns.catalogue_loader`), marked
`x-srg-practice: true`; BoG publishes the real ones through the return definitions
workflow.

## Part 3 — Supervisor API (`/portal/api`, BoG staff only)

Bearer: the BoG identity provider's ID token (see ARCHITECTURE.md). Every response
is scoped to what the caller's role may see. Every read of a submission, query or
institution record is an audit entry (`category: "view"`).

```ts
type Me = { subject: string; name: string; email: string; roles: Role[] };
type Role = "supervisor" | "senior_supervisor" | "returns_policy" | "access_approver" | "auditor";
type Channel = "api" | "portal_upload";
type SubmissionRow = {
  receipt_id: string; received_at: string; institution_code: string; institution_name: string;
  return_code: string; return_name: string; reporting_date: string; version: number;
  channel: Channel; software_declared: string | null;            // "AequorOS 2026.11"
  validation: { passed: number; total: number; failed: number } | null;
  status: SubmissionState; reviewer_name: string | null;          // "Automatic" for system decisions
};
```

- `GET /portal/api/me` → `Me`
- `GET /portal/api/overview?reporting_date=YYYY-MM-DD` →
  ```ts
  { reporting_date: string;
    totals: { due: number; received: number; accepted: number; outstanding_past_due: number; filed_late: number; open_queries: number; institutions: number; returns: number };
    heatmap: { return_codes: string[]; rows: { institution_code: string; institution_name: string;
               cells: { return_code: string; state: "on_time" | "late" | "outstanding" | "not_due"; receipt_id: string | null }[];
               received: number; due: number }[] };
    attention: { kind: "outstanding" | "rejected" | "queried" | "late" | "credential"; when: string; title: string; detail: string;
                 link: { type: "submission" | "institution" | "query" | "onboarding"; ref: string } }[];
    channel: { api_availability_30d: number | null; median_receipt_seconds: number | null; filings_today: number; certificates_expiring_30d: number };  // null: nothing measured yet
    latest: SubmissionRow[] }
  ```
- `GET /portal/api/submissions?status=&return_code=&institution_code=&reporting_date=&channel=&q=&cursor=`
  (`status` is one of the `counts` keys other than `all`; omitted means all) →
  `{ items: SubmissionRow[]; next_cursor: string | null; counts: Record<"all" | "needs_review" | "validating" | "rejected" | "queried" | "accepted", number> }`
- `GET /portal/api/submissions/{receipt_id}` →
  ```ts
  { row: SubmissionRow; return_version: string; basis: "solo" | "consolidated";
    amendment: { supersedes_receipt_id: string; supersedes_received_at: string; reason: string } | null;
    validation: { rule_set: string; ran_at: string; duration_ms: number;
                  groups: { label: string; passed: number; total: number; note: string }[]; issues: Issue[] };
    content: { currency: string; unit: string;   // unit: "" when each section states its own
               sections: { code: string; title: string; unit: string }[];
               lines: { code: string; label: string; value: string | null; previous_version_value: string | null; prior_period_value: string | null; is_total: boolean; changed: boolean; section: string | null }[] };
    evidence: { receipt_id: string; payload_sha256: string; signature: { valid: boolean; alg: string; kid: string; key_registered_at: string; custody_note: string };
                certificate: { valid: boolean; not_after: string }; receipt_signature: { valid: boolean; kid: string };
                received_at: string; ledger_seq: number; chain_intact: boolean };
    declaration: { prepared_by_role: string; approved_by_role: string; approved_at: string; software: string | null };
    institution: { code: string; name: string; type: string; supervisor_name: string | null; cycle_received: number; cycle_due: number; outstanding: string[] };
    versions: { version: number; receipt_id: string; received_at: string; status: SubmissionState; current: boolean }[];
    activity: { at: string; title: string; detail: string }[];
    allowed_actions: ("accept" | "query" | "request_resubmission" | "assign")[];
    documents: { name: string; media_type: string; sha256: string; bytes: number }[] }
  ```
- `GET /portal/api/submissions/{receipt_id}/receipt` → `200 application/json` attachment
  `{receipt_id}-receipt.json`: the `ReceiptEnvelope` the institution's own
  `GET /v1/submissions/{receipt_id}/receipt` returns. Writes a `view` audit entry
  ("Downloaded receipt").
- `GET /portal/api/submissions/{receipt_id}/documents/{name}` → the filed document, byte for
  byte, as an attachment. Writes a `view` audit entry ("Downloaded filed document").
- `GET /portal/api/submissions/{receipt_id}/package` → `200 application/zip` attachment
  `{receipt_id}-filed-package.zip`: the filing as received, verifiable without the gateway —
  `manifest.json`, `payload.json`, `declaration.json` (JCS bytes), `documents/*` (the filed
  Excel, CSV and PDF), `signature.jws` (the institution's ES256 JWS over `manifest.json`),
  `receipt.json` + `receipt.jws`, `ledger-entry.json` (the hash-chained, signed ledger entry
  the receipt names), `transport.json` (API client and mTLS certificate fingerprint),
  `institution-signing-key.json`, `gateway-receipt-keys.json` and `gateway-audit-keys.json`
  (JWKS), `validation.json`, `VERIFY.txt`, `index.json` (SHA-256 of every file) and
  `index.jws` (the gateway's ES256 signature over `index.json`, typ
  `srg-filed-package+jws`). Writes a `view` audit entry ("Downloaded filed package").
- `POST /portal/api/submissions/{receipt_id}/accept` `{ note?: string }` → `204`
- `POST /portal/api/submissions/{receipt_id}/resubmission-request` `{ reason: string; due_at: string }` → `201 { number: string }`
- `POST /portal/api/submissions/{receipt_id}/assign` `{ reviewer_subject: string }` → `204`
- `GET /portal/api/queries?status=open|awaiting_institution|awaiting_supervisor|closed` →
  `{ items: { number: string; institution_name: string; subject: string; status: "awaiting_institution" | "awaiting_supervisor" | "overdue" | "closed"; kind: "query" | "resubmission"; return_code: string; due_at: string | null; last_activity_at: string }[]; counts: Record<"open" | "awaiting_institution" | "awaiting_supervisor" | "closed", number>; next_cursor: string | null }`
- `GET /portal/api/queries/{number}` → `{ number; subject; kind; status; institution_code; institution_name; receipt_id; return_code; reporting_date; opened_by: string; opened_at; due_at; delivered_at: string | null; acknowledged_at: string | null; messages: (QueryMessage & { author_role: string | null })[] }`
- `POST /portal/api/queries` `{ receipt_id: string; subject: string; body: string; kind: "query" | "resubmission"; due_at: string }` → `201 { number }`
- `POST /portal/api/queries/{number}/messages` `{ body: string; require_resubmission: boolean; due_at: string | null }` → `201 QueryMessage`
- `POST /portal/api/queries/{number}/close` → `204`
- `GET /portal/api/institutions?q=` → `{ items: { code; name; type; status: "enrolled" | "live" | "suspended"; channel: Channel | null; software_declared: string | null; on_time_12m: number | null; outstanding: number }[] }`
- `GET /portal/api/institutions/{code}` →
  ```ts
  { code; name; type; channel: Channel | null; flags: string[];                 // e.g. ["heightened_monitoring"]
    stats: { on_time_12m: number; peer_median_on_time_12m: number; outstanding: number; late_this_year: number; filings_this_year: number; open_queries: number; last_filing: { at: string; label: string } | null };
    history: { months: string[]; rows: { return_code: string; cells: { reporting_date: string; state: "on_time" | "late" | "outstanding" | "not_due"; receipt_id: string | null }[] }[] };
    obligations: Obligation[];
    contacts: { role: string; name: string }[];
    credentials: { portal_users: number; mfa: boolean; api_client: { client_id: string; certificate_not_after: string } | null; signing_keys: { kid: string; status: string }[] };
    supervisor_name: string | null;
    notes: { id: string; author_name: string; body: string; created_at: string }[] }
  ```
- `POST /portal/api/institutions/{code}/notes` `{ body: string }` → `201`
- `GET /portal/api/calendar?month=YYYY-MM` → `{ month: string; days: { date: string; events: { title: string; detail: string; tone: "past" | "warning" | "future"; received: number | null; due: number }[] }[]; upcoming: { label: string; count_label: string; detail: string }[]; cadences: { cadence: string; returns: number }[] }`
- `GET /portal/api/returns` → `{ items: (ReturnSummary & { rule_count: number; status: "published" | "draft_pending" })[] }`
- `GET /portal/api/returns/{code}` → `ReturnDetail & { draft: { version: string; change_summary: string; first_reporting_date: string; notice_days: number; breaking: boolean; approvals: { step: "drafted" | "approved" | "pending"; by: string | null; role_label: string; at: string | null }[] } | null }`
- `POST /portal/api/returns/{code}/drafts` `{ version: string; change_summary: string; first_reporting_date: string; notice_days: number; schema: object; rules: Rule[] }` → `201`
- `POST /portal/api/returns/{code}/versions/{version}/approve` → `204` (second distinct approver publishes)
- `POST /portal/api/returns/{code}/versions/{version}/return` `{ reason: string }` → `204`
- `GET /portal/api/onboarding` (read-only: the institutions on the channel; enrolment is the operator's, Part 5) → `{ stages: { stage: "enrolled" | "live" | "suspended"; count: number }[]; register: { code; name; type; channel: Channel | null; software_declared: string | null; enrolled_at: string; client_id: string | null; certificate_not_after: string | null; certificate_expiring: boolean; signing_kid: string | null; status: "enrolled" | "live" | "suspended" }[] }`
  (`enrolled`: enrolled by the operator, nothing filed yet; `live`: has filed; `suspended`: its clients cannot obtain tokens)`
- `GET /portal/api/audit?actor_type=&category=&institution_code=&from=&to=&cursor=` → `{ integrity: { intact: boolean; entries: number; verified_at: string; head_hash: string }; siem: { streaming: boolean; lag_seconds: number | null }; retention_label: string; items: { seq: number; occurred_at: string; actor_label: string; actor_via: string; category: string; action: string; object_ref: string }[]; next_cursor: string | null }`
- `GET /portal/api/audit/{seq}` → `{ seq; occurred_at; actor_label; actor_via; category; action; object_ref; detail: Record<string, unknown>; prev_hash: string; entry_hash: string; signature_kid: string; signature_valid: boolean }`
- `POST /portal/api/audit/verify` → `{ intact: boolean; entries: number; verified_at: string; first_bad_seq: number | null }`
- `GET /portal/api/audit/export?from=&to=` → signed export bundle (`application/zip`: entries JSONL + JWKS + verification manifest)
- `GET /portal/api/access` → `{ roles: { group: string; role: Role; can: string; members: number | null }[]; pending: { number: string; operator_name: string; operator_title: string; scope: string; excludes: string; reason: string; incident_ref: string | null; requested_at: string; expires_at: string }[]; sessions: { number: string; operator_name: string; status: "active" | "ended" | "denied" | "expired"; detail: string }[] }`
- `POST /portal/api/access/requests/{number}/approve` `{ duration_minutes: 30 | 60 | 120 }` → `204`
- `POST /portal/api/access/requests/{number}/deny` `{ reason: string }` → `204`

## Part 4 — Development identity provider (`local|test` only)

`/dev/oidc/.well-known/openid-configuration`, `/dev/oidc/authorize` (a page listing
the synthetic staff to sign in as), `/dev/oidc/token`, `/dev/oidc/jwks`. ID token
claims: `sub`, `name`, `email`, `groups`. Synthetic staff: Kofi Owusu
(`srg-senior-supervisors`), Akosua Boateng (`srg-supervisors`), Esi Asante
(`srg-returns-policy`), Michael Adjei (`srg-returns-policy`,
`srg-access-approvers`), Internal Audit (`srg-internal-audit`). Any other
environment returns `404` for every `/dev/*` path.

## Part 5 — Operator API (`/ops/api`, the channel operator's machine client only)

Enrolment belongs to the channel operator, never to BoG staff. The operator client
is an API client of kind `operator`, configured at deployment through
`GATEWAY_OPERATOR_CLIENTS` (JSON list: `client_id`, `jwks`, `certificate_sha256`),
and authenticates like an institution client: mutual TLS, `POST /oauth/token` with
`private_key_jwt`, scope `ops:enrol`, certificate-bound token. Institution clients
can never obtain `ops:enrol`; operator clients can never file. In `local|test` the
service creates one development operator client on first start and writes its key,
certificate and client id to `api/.keys/operator/` so a local AequorOS can enrol
banks. Every call is an audit entry with `actor_type: "operator"`. All writes are
idempotent where stated, so a retried enrolment never duplicates anything.

```ts
type InstitutionType = "universal_bank" | "rural_bank" | "savings_and_loans" | "microfinance" | "other";
type EnrolledInstitution = {
  code: string; name: string; type: InstitutionType;
  status: "enrolled" | "live" | "suspended";
  enrolled_at: string; clients: { client_id: string; environment: "sandbox" | "production"; certificate_not_after: string; revoked: boolean }[];
  signing_keys: { kid: string; status: "active" | "retired" | "revoked"; registered_at: string }[];
};
```

- `POST /ops/api/institutions` `{ code: string; name: string; type: InstitutionType; contacts: { role: string; name: string; email: string }[] }`
  → `201 EnrolledInstitution` (idempotent on `code`: the same body returns `200` with the existing record; a different name or type is `409 conflict`)
- `GET /ops/api/institutions?limit=&cursor=` → `{ items: EnrolledInstitution[]; next_cursor: string | null }` (by code; the cursor is the last code returned)
- `GET /ops/api/institutions/{code}` → `EnrolledInstitution`
- `POST /ops/api/institutions/{code}/clients` `{ environment: "sandbox" | "production"; csr_pem: string; jwks: { keys: object[] }; software: { name: string; version: string } | null }`
  → `201 { client_id: string; certificate_pem: string; certificate_chain_pem: string; not_after: string; scopes: string[] }`
  (the gateway CA signs the CSR; the CSR's public key must equal the JWKS key used for client assertions; scopes are the institution filing scopes)
- `POST /ops/api/clients/{client_id}/revoke` `{ reason: string }` → `204`
- `POST /ops/api/institutions/{code}/signing-keys` `{ kid: string; public_jwk: object }` → `201` (idempotent on `kid` + same key; a different key under an existing `kid` is `409 conflict`); registering a new key does not retire the old one
- `POST /ops/api/institutions/{code}/signing-keys/{kid}/retire` → `204` (filings already signed with it stay verifiable)
- `POST /ops/api/institutions/{code}/status` `{ status: "live" | "suspended"; reason: string }` → `204` (a suspended institution's clients cannot obtain tokens)
- `POST /ops/api/access-requests` `{ operator_name; operator_title; scope; excludes; reason; incident_ref }` → `201 { number }` (support-session request for BoG's approval)
