# AequorOS Reporting Gateway — architecture

The gateway is our own regulatory reporting channel: institutions file regulatory
returns into it through one public API, and Bank of Ghana (BoG) supervisors sign in
on a subdomain BoG configures for us to receive, review, query and accept those
filings. We (the company behind AequorOS) operate it, so our client banks can report
to BoG without depending on ORASS. It is deployed and run separately from AequorOS;
AequorOS connects to it through the same public API any other software would use.

## Commitments the design must keep

1. **Operated by us, configured for the regulator.** We run the service and enrol
   institutions. BoG owns its subdomain and signs its staff in through its own
   identity provider (OIDC); BoG staff receive and review, they never administer
   institutions or credentials. BoG approves every operator support session.
2. **Effortless for institutions.** An AequorOS client bank is enrolled
   automatically when our team onboards it; the bank has nothing to request,
   install or configure. The public API stays open, so other software can be
   enrolled the same way later.
3. **Evidence-grade.** Every filing carries the institution's signature and is
   receipted by the gateway; nothing filed is ever changed or deleted; corrections
   are new versions.
4. **Open and auditable.** The API contract, return schemas and change history are
   published and versioned; every action is in a verifiable hash chain.
5. **Exit-ready.** BoG can export everything and run the system without us.

## Hard boundaries

- Nothing under `gateway/` imports from `backend/`, `console/` or any AequorOS
  package, and nothing in AequorOS imports from `gateway/`. They share no database,
  key, credential or runtime. The only link is the public HTTP API.
- The gateway has its own Postgres database and its own Alembic chain.
- Synthetic demo data exists only for `GATEWAY_ENV=local|test|sandbox`; seeding
  refuses to run in `production`.

## Components

| Path | What it is | Port (local) |
| --- | --- | --- |
| `api/` | FastAPI service: institution API (`/v1`), supervisor API (`/portal/api`), OAuth token endpoint, dev identity provider (local only) | 8200 |
| `portal/` | Next.js supervisor portal for BoG staff (the approved design) | 3100 |
| `contract/` | The specification (`SPEC.md`) and the OpenAPI documents exported from `api/` | — |

`deploy/` holds the Coolify stack's setup (`deploy/README.md`) beside
`docker-compose.prod.yml`. Later: `institution-portal/` (upload and queries for
institutions without an API integration), `docs/` (developer documentation site).

## Identities and authentication

### Enrolment (operator only)

Institutions, their API clients and their signing keys are created only through the
**operator API** (`/ops/api`, `contract/SPEC.md` Part 5), by the channel operator's
own machine client. BoG staff have no endpoint that creates or changes any of them.
For an AequorOS client bank the operator client is the AequorOS platform: our staff
console enrols the bank, AequorOS generates the bank's client key and signing key,
the gateway issues the client certificate from its own CA, and the bank sees the
channel simply as connected.

In the staff console: open the tenant, start an inspection session (enrolment writes
into that tenant's configuration, so it is gated and audited like its encryption
key), then use **AequorOS Reporting Gateway → Enrol on the gateway** with the bank's
four-digit institution code (blank uses its institution profile). One idempotent call
(`POST /operator/v1/tenants/{org}/banks/{bank}/reporting-gateway/enrolment`) creates
the bank's client key and signature key in the platform vault, enrols the institution,
has the gateway issue the client and register the key, and tests the connection as
the bank. "Enrol again" repairs a partial enrolment without issuing anything twice.

**Signing-key custody.** The gateway sees an institution's signing key only as a
registered public key. For AequorOS banks the private key is a separate key per
bank held by the AequorOS platform, used only to sign a return after the bank's own
preparer and approver have completed its approval workflow; AequorOS staff cannot
trigger it. A bank or BoG may later require a key held in the bank's own key
service; the gateway needs no change for that.

### Institutions (machine filing) — four layers, FAPI 2.0 aligned

1. **Mutual TLS.** Each API client has a client certificate issued by the gateway's
   CA at enrolment.
   TLS terminates at the ingress, which verifies the certificate against the
   configured CA and forwards it in `X-Forwarded-Tls-Client-Cert` (URL-encoded PEM).
   The API trusts that header only from `GATEWAY_TRUSTED_PROXIES`; in
   `local|test` it may be injected directly. The client is identified by the
   certificate's SHA-256 thumbprint (`x5t#S256`, base64url).
2. **OAuth 2.0 client credentials with `private_key_jwt`.** `POST /oauth/token`
   requires the client certificate and a client assertion JWT signed with a key in
   the client's registered JWKS (`iss`=`sub`=client_id, `aud`=the token endpoint
   URL, unique `jti`, `exp` ≤ 5 minutes ahead). No shared secrets exist.
   The access token is a gateway-signed ES256 JWT, valid 600 s, carrying
   `client_id`, `institution_code`, `scope` and `cnf: {"x5t#S256": …}`.
   Every API call must present the same certificate the token is bound to.
   Scopes: `returns:read`, `obligations:read`, `submissions:write`,
   `submissions:read`, `queries:read`, `queries:write`.
3. **Institution content signature.** Each filing carries a compact JWS (ES256)
   whose payload is the *filing manifest* (see `contract/SPEC.md`), signed with the
   signing key registered for the institution at enrolment (`kid`). The gateway
   verifies the JWS, that every manifest field matches the request, and that
   `payload_sha256` matches the payload.
4. **Gateway receipt.** The gateway signs a receipt (compact JWS, ES256) with its
   receipt key; the public keys are served at `GET /v1/.well-known/jwks.json`, so
   anyone holding a receipt can verify it offline.

### BoG staff (supervisor portal)

The portal performs the OIDC authorization-code flow with PKCE against BoG's
identity provider and calls the supervisor API with the ID token as a Bearer
token. The API validates it against the provider's JWKS (`GATEWAY_OIDC_ISSUER`,
audience `GATEWAY_OIDC_AUDIENCE`) and maps the `groups` claim to roles. No staff
password is ever held. In `local|test`, the API serves a development provider at
`/dev/oidc` with synthetic staff (refused in any other environment).

| Group | Role | Can |
| --- | --- | --- |
| `srg-supervisors` | supervisor | review, accept, query, request resubmission for assigned institutions |
| `srg-senior-supervisors` | senior_supervisor | supervisor rights for all institutions; reassign |
| `srg-returns-policy` | returns_policy | draft and approve return definitions (two different approvers) |
| `srg-access-approvers` | access_approver | approve or deny operator sessions |
| `srg-internal-audit` | auditor | read everything, decide nothing, export audit log |

Every staff role can read the institutions on the channel; none can enrol, change
or suspend one. Separation of duties is enforced in the service, not the UI: a
return version's drafter and its two approvers are three different people; nobody
approves an operator session they requested; auditors cannot perform any decision.

### Operators (the channel operator: us)

Two separate things. **Enrolment** is done by the operator's machine client through
`/ops/api`: an API client of kind `operator`, configured at deployment
(`GATEWAY_OPERATOR_CLIENTS`), authenticating exactly like an institution (mutual TLS
+ `private_key_jwt` + certificate-bound token) with scope `ops:enrol`. **Support
access** for people has no standing access: an operator requests a time-boxed
session with a scope and a reason; a BoG access approver approves or denies it; it
expires automatically. Every enrolment call and every support step is an audit entry
with `actor_type: "operator"`.

## Keys

Two gateway keys, both ES256, loaded through a `KeyProvider` interface
(`local` file provider for `local|test|sandbox`; a cloud KMS/HSM provider for
production, keys held in BoG's key service):

- **receipt key** — signs receipts and access tokens; published in the JWKS.
- **audit key** — signs audit chain entries.

Key ids (`kid`) are stable strings such as `gateway-receipt-2026-q4`. Rotation adds
a new key to the JWKS before use and keeps the old one published for verification.

## Evidence: the audit chain

Every state change and every sensitive read is an `audit_entries` row:
`seq` (gapless bigint), `occurred_at`, `actor_type` (`staff|institution|system|operator`),
`actor_ref`, `category` (`sign_in|filing|decision|view|query|configuration|credential|operator_access|system`),
`action`, `object_type`, `object_ref`, `detail` (JSON), `prev_hash`, `entry_hash`,
`signature`.

`entry_hash = sha256(prev_hash_hex + "\n" + JCS(entry without hashes and signature))`
where JCS is RFC 8785 canonical JSON; the first entry's `prev_hash` is 64 zeros.
`signature` is the audit key's ES256 signature over `entry_hash`. Appends take a
transaction-level advisory lock so `seq` and the chain stay linear. Rows are never
updated or deleted: the database role the API runs as has no UPDATE/DELETE on the
table, and a trigger refuses both. `POST /portal/api/audit/verify` recomputes the
chain; a scheduled job does the same every 30 minutes.

## Filing lifecycle

```
received → validating → rejected            (failed blocking rules; institution refiles as a new version)
                      → in_review → accepted
                                  → queried  → in_review (after the institution answers)
any non-final version → superseded          (a newer version for the same institution, return, reporting date, basis)
```

Status changes are made only by the service; every transition is an audit entry.
A submission row is immutable except `status`, `reviewer_id` and the timestamps of
its decisions; the payload, manifest, signature, digests and receipt never change.

## Data model (gateway database)

`institutions`, `institution_contacts`, `api_clients`, `signing_keys`,
`return_definitions`, `return_versions`, `return_version_approvals`, `obligations`,
`submissions`, `receipts`, `queries`, `query_messages`, `supervisory_notes`,
`staff_users`, `operator_access_requests`, `audit_entries`. Column-level detail is
the API's to own; the response shapes in `contract/SPEC.md` are the contract.

## Environments

`GATEWAY_ENV` is one of `local`, `test`, `sandbox`, `production`. `sandbox` is the
public environment institutions certify against: same code, separate database,
separate keys, synthetic reference data. `production` refuses every development
convenience (dev identity provider, header-injected certificates, seeding).
