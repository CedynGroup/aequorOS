# Q03 — "Production-ready" live vendor auth is unverifiable in this environment

**Deciding:** what "production-ready Bloomberg/Refinitiv adapter" can mean when the live
vendor path cannot be exercised here.

**What the docs say (they agree with the constraint):**
- `market_data_adapter.md` §6.6: *"The vendor certification process is separate from the
  software. Building the adapter does not require completed vendor certification."*
- §5.5 / §7.4: fixtures are the sanctioned dev/CI path; live connection tests are separate
  and gated on vendor access.
- §14.1: live production pulls are explicitly deferred pending a pilot bank's credentials.

**The hard reality:** true live verification needs the Bloomberg `blpapi` SDK + a B-PIPE/
Data-License entitlement and a Refinitiv RDP client-id/secret. None are available in this
environment, and `blpapi` is not installable without Bloomberg entitlement. So I can build
and unit/contract-test the full auth/session/retry/backoff/pooling/rate-limit/catalog/
extractor/translator code, but I **cannot** prove a real handshake against a live endpoint.

**Default I'm proceeding on:** build the adapters as **production-shaped code, verified
against recorded fixtures and the shared contract-test suite** (the docs' own standard),
with the live transport isolated behind a seam that raises the classified
`VENDOR_UNAVAILABLE` / `CREDENTIAL_INVALID` codes until real credentials are wired. Every
catalog entry whose vendor field/RIC I cannot confirm from public docs is marked
`verification_required: true` (never invented, never skipped), per the brief.

**Need from Eric/Dela to lock:** accept "fixture-verified, live-verify pending
credentials" as the definition of done for these adapters at this stage. If a live
handshake is required before sign-off, provide a sandbox credential set (or confirm the
Temenos/vendor sandbox access track), and note `blpapi` must be vendored from Bloomberg.
