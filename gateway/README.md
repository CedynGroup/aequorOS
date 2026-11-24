# AequorOS Reporting Gateway

The regulator-side reporting system: institutions file regulatory returns through a
public API; Bank of Ghana supervisors review, query and accept them in the portal.

- `ARCHITECTURE.md` — trust model, authentication, keys, evidence chain, lifecycle
- `contract/SPEC.md` — the API contract (institution `/v1` and supervisor `/portal/api`)
- `AGENTS.md` — rules for anyone changing this folder
- `api/` — gateway service (FastAPI), port 8200
- `portal/` — supervisor portal (Next.js), port 3100
- `design/` — the approved UI design (one `.dc.html` per screen)

This folder is self-contained: it shares no code, database, key or runtime with
AequorOS and can move to its own repository unchanged.
