# aequorOS transport security and bank evidence

This is the repository control contract for issue #394, correcting the blanket
TLS 1.3 assertion identified in #356. The minimum is **TLS 1.2**, with TLS 1.3
negotiated where both peers support it. A source-code setting is not evidence
of a deployed handshake. No deployed environment was changed or probed by this
implementation; deployment evidence remains a release acceptance requirement.

## Connection inventory

| Hop                                                                                 | Enforcement and verification                                                                                                                                                                                                                                                                                   | Minimum / evidence                                                                                                                                                                             |
| ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Browser → edge → API or staff service                                               | Edge HTTPS and HTTP redirect; HSTS middleware. Origin uses `app.core.serve` with a certificate and key. Deployed ASGI apps reject plaintext before authentication, including requests spoofing `X-Forwarded-Proto`.                                                                                            | TLS 1.2 minimum at origin; edge minimum must be configured and independently probed.                                                                                                           |
| Edge → API, operator, dashboard, console, signing vault                             | Origin listeners accept TLS. Proxy must use HTTPS upstreams and the matching verified `serversTransport` from `deploy/tls/traefik.example.yml`; a trust bundle must also be mounted in the proxy.                                                                                                              | TLS 1.2; test each origin directly from the proxy network.                                                                                                                                     |
| API / operator / core, BI and AI workers / migrations → PostgreSQL                  | All application pools and Alembic require `sslmode=verify-full`, a hostname, a trusted CA, and minimum TLS 1.2. GSS encryption preference is disabled so evidence concerns actual TLS. BI and operator override URLs are covered.                                                                              | Capture `pg_stat_ssl` for each configured database role. Encryption-only `require` and `verify-ca` are refused.                                                                                |
| API / Data Engine / staff provisioning → object storage; browser presigned transfer | Configured and SDK-resolved endpoints require HTTPS. Both S3 client implementations and provisioning explicitly enable certificate verification. Presigned URLs inherit the verified endpoint. AWS KMS provisioning and bank-key operations follow the same policy.                                            | TLS 1.2 minimum in the pinned Python/urllib3 runtime; probe the object-store endpoint from the app and browser network.                                                                        |
| Application → OpenBao; proxy → OpenBao                                              | Verified HTTPX context; optional `OPENBAO_CA_CERT` loads private roots. No redirects or environment proxy overrides. OpenBao listener uses certificates and `tls_min_version = "tls12"`. Its CLI healthcheck verifies the listener CA and hostname.                                                            | Capture application and origin negotiations separately. OpenBao's Raft cluster transport uses its own authenticated TLS; collect cluster evidence separately if more nodes are introduced.     |
| AI → Anthropic / OpenAI / Gemini                                                    | TLS 1.2 context with hostname and certificate verification; redirects refused, environment proxies disabled. Anthropic's endpoint is explicitly pinned rather than accepting a plaintext environment override.                                                                                                 | Capture each enabled provider from the AI worker network.                                                                                                                                      |
| Signing → TSA                                                                       | pyHanko protocol over a verified HTTPX client; TLS 1.2, no redirects, finite timeout, no plaintext private-link exception in a deployed environment. Only the digest is transmitted.                                                                                                                           | Probe the configured TSA and retain a successful synthetic timestamp operation. TLS peer trust is separate from timestamp-token signer trust.                                                  |
| Signing → issuer certificate / OCSP / CRL                                           | pyHanko's parsing and caching use verified HTTPX transports for validation-material collection. HTTP endpoints and redirects are refused on deployed services; verification itself remains offline.                                                                                                            | TLS 1.2; certificate profiles must advertise HTTPS validation endpoints. Retain synthetic LTV signing evidence.                                                                                |
| OIDC discovery / JWKS / staff token exchange                                        | HTTPS destination guard and verified TLS. Python uses the shared TLS 1.2 context; staff discovery validates returned authorization and token endpoints and refuses redirects during credential exchange.                                                                                                       | Capture each configured IdP endpoint; existing SSRF controls still apply.                                                                                                                      |
| Research / regulatory channel HTTP                                                  | Certificate bypass for research sources removed; redirect hops are checked. ORASS refuses `verify_tls=false` and uses the shared verified context. A broken upstream chain fails capture/submission.                                                                                                           | Probe upstreams from the worker network; supply the correct CA chain, never disable verification.                                                                                              |
| Worker → SMTP                                                                       | STARTTLS required before login or message delivery; `ssl.create_default_context` verifies hostname and certificate, minimum TLS 1.2. A missing STARTTLS extension fails delivery.                                                                                                                              | Retain STARTTLS negotiation evidence and a synthetic delivery result. SMTP greeting and STARTTLS negotiation are plaintext protocol preamble; credentials and content are sent only after TLS. |
| Database-Direct → bank core                                                         | Native Oracle and SQL Server drivers require enabled TLS and certificate verification on deployed workers; Snowflake uses the verified connector default. Generic JDBC/ODBC encryption hints do not prove certificate verification and are refused on deployed workers until a verified driver profile exists. | Collect native vendor handshake and negative-certificate evidence during connector onboarding.                                                                                                 |
| Cache / queue                                                                       | No network cache service exists in this repository. Jobs and authorization caches use PostgreSQL or process memory.                                                                                                                                                                                            | No Redis hop to assert; any new network cache must add verified TLS enforcement and evidence before activation.                                                                                |

## Local development and narrowly bounded exceptions

`TLS_ALLOW_PLAINTEXT=1` is explicitly allowed only with `APP_ENV=local` or `test`.
The checked-in local `.env.example` and hermetic test fixture select it; it is
not a default. Staging and production refuse startup when it is selected.
The shared HTTPS client contexts always verify certificates and hostnames;
the local switch permits HTTP without weakening those contexts. It also bypasses
the PostgreSQL and Database-Direct transport guards for local/test connections.

The Next.js container gateway accepts TLS on its network port and forwards to
Next.js on **127.0.0.1 inside the same container**. That loopback socket is the
only production HTTP exception. It carries no cross-container or host-network
traffic; the Next server binds only loopback. This exception must appear in the
bank's deployment inventory. PostgreSQL's SSLRequest and SMTP's STARTTLS
preambles are protocol negotiation, not an authorization to send plaintext data.
There are no private-network plaintext exceptions for service-to-service hops.

## Deployment preparation (review before applying)

The compose changes are templates requiring a coordinated TLS rollout. They
are not safe to deploy onto an unprepared proxy or certificate store. This task
does not create volumes, install certificates, change Coolify, restart services,
or update any deployed environment.

1. Provision an **external** Docker volume named `aequoros-service-trust` on each
   target host with `ca.pem` (public trust roots plus required private roots).
   Supply certificates and private keys in separate external volumes named
   `aequoros-api-tls`, `aequoros-operator-tls`, `aequoros-dashboard-tls`,
   `aequoros-console-tls`, and `aequoros-openbao-tls` as needed: `api.crt`/`api.key`,
   `operator.crt`/`operator.key`, `dashboard.crt`/`dashboard.key`,
   `console.crt`/`console.key`, and `openbao.crt`/`openbao.key` as needed.
   Each leaf certificate needs its service DNS SAN: `risk-api`, `risk-operator`,
   `dashboard`, `console`, or `openbao`. Certificates used by application clients
   also need the application-facing hostname. Keys must be readable only by the
   intended service UID; workers and proxies mount only the trust volume. Templates mount the volume read-only; never put keys in the repository.
2. Set each database URL to a PostgreSQL/psycopg URL with `sslmode=verify-full`.
   Private roots may be selected with `sslrootcert` in that URL or with
   `TLS_CA_BUNDLE`. All production compose services pin `APP_ENV=production`
   and refuse the local plaintext switch. Use HTTPS storage/vault/TSA URLs.
3. Install the proxy file-provider transports, separately mount its CA bundle,
   and attach the correct transport to each HTTPS upstream service. Set upstream
   scheme **https**. Never use `insecureSkipVerify`, even on a private network.
   Enable HTTPS routers, HTTP redirects, HSTS, and an edge TLS minimum of 1.2.
   Verify the generated Coolify/Traefik configuration rather than trusting labels
   that may have been rewritten. Public certificate automation does not supply
   or authenticate origin certificates automatically.
4. For dashboard and console, set runtime `NODE_EXTRA_CA_CERTS` and HTTPS service
   URLs. Set the dashboard's build-time `NEXT_PUBLIC_RISK_API_BASE_URL` to HTTPS
   and rebuild; changing runtime env alone cannot change the browser bundle.
   `serve-next.cjs` refuses plaintext service URLs and
   `NODE_TLS_REJECT_UNAUTHORIZED=0`. The console now has its own TLS compose
   template. Development still uses `pnpm dev`; local staff HTTP requires
   `TLS_ALLOW_PLAINTEXT=1` explicitly.
5. Perform an isolated acceptance deployment first. Check expiry and SANs, proxy
   hostname verification, service UID permissions, readiness, synthetic uploads,
   login, signing, enabled AI and SMTP. Rotate certificates through the external
   volume and restart listeners as a coordinated release; no hot reload is claimed.

## Bank-reviewable evidence pack

Keep the pack private: hostnames, certificate chains, role names and deployment
configuration are infrastructure evidence, not public repository artifacts.
Record the release commit, image digests, UTC capture time, vantage point,
peer hostname, minimum configured version, negotiated version and cipher,
certificate fingerprint, issuer/expiry/SAN checks, trust-store identity, and
reviewer. Store proxy-generated configuration and negative-test results with it.

Run the read-only collector **from every relevant client network**, from the
`backend/` directory and using the same trust bundle as that client:

```bash
uv run python -m app.core.tls_evidence \
  --https https://edge.example \
  --https https://risk-api:8000 \
  --https https://risk-operator:8100 \
  --https https://object-store.example \
  --https https://openbao:8200 \
  --https https://tsa.example \
  --databases > tls-evidence.json
```

Repeat for dashboard, console, enabled AI providers, IdPs and regulatory/research
endpoints. The collector authenticates the peer, records the certificate hash
and negotiated TLS version, queries only the current PostgreSQL connection's
`pg_stat_ssl` row, and exits nonzero on failures. It never prints database URLs,
credentials, HTTP paths or raw driver errors. Direct TLS sockets do not prove a
proxy's own upstream-verification setting; retain the generated proxy config and
run a wrong-SAN/untrusted-certificate upstream failure test in the isolated
acceptance environment as well. For SMTP and optional native bank drivers,
retain vendor-specific negotiation evidence. Scan edge and origin listeners to
prove TLS 1.0/1.1 are refused, rather than inferring that from a successful 1.3
handshake. Do not transmit bank records for any evidence test.

`--databases` resolves configured service database URLs through the application
settings, including `.env` and environment overrides. Unconfigured URLs are
skipped; record which roles share a fallback pool and confirm the pack covers
every configured role.

Repository verification:

```bash
mise run risk-service:check
node --test deploy/tls/serve-next.test.cjs
pnpm --filter @aequoros/console typecheck
pnpm --filter @aequoros/dashboard typecheck
```

`tests/core/test_tls.py` exercises production startup refusals, database downgrade
options, the local-only exception, listener key requirements, rejection of
spoofed plaintext ASGI requests, and real loopback certificate handshakes for
untrusted roots, wrong hostnames and expired certificates. These tests prove
code behavior; they do not replace deployed measurements. A bank should receive
both the passing release tests and the completed deployment evidence pack.

The control choices follow the upstream contracts for
[PostgreSQL certificate and hostname verification](https://www.postgresql.org/docs/17/libpq-ssl.html),
[HTTPX verified SSL contexts](https://www.python-httpx.org/advanced/ssl/),
[OpenBao TLS listeners](https://openbao.org/docs/configuration/listener/tcp/), and
[Traefik verified origin transports](https://doc.traefik.io/traefik/v3.6/reference/routing-configuration/http/load-balancing/serverstransport/).
