# Gateway — agent rules

The AequorOS Reporting Gateway is the regulator-side system. Read
`ARCHITECTURE.md` (what and why) and `contract/SPEC.md` (the API contract) before
writing code. These rules hold for every change under `gateway/`.

## Boundaries

- Nothing under `gateway/` imports from `backend/`, `console/`, `frontend/` or
  `packages/` at the repository root, and it is not part of the root pnpm
  workspace or the root `mise.toml`. It must build, test and run with only
  `gateway/` present, because it may move to its own repository unchanged.
- The only contract with AequorOS is the public `/v1` API in `contract/SPEC.md`.
  No AequorOS-specific code path, field or shortcut, ever.
- Never commit, push or open a PR. Leave changes in the working tree and report.

## Stack

| Part | Stack |
| --- | --- |
| `api/` | Python 3.13, a uv project of its own (`pyproject.toml`, `uv.lock`), FastAPI, SQLAlchemy 2, Alembic, psycopg 3, Pydantic v2, `cryptography` and PyJWT for ES256 JWS/JWT. Ruff and basedpyright in **strict** mode (including `reportAny`) for `app/`; tests use pytest. |
| `portal/` | Next.js 16.4.5, React 19.3.0, TypeScript strict, Tailwind CSS 4, oxlint + eslint, tests with `node --test` (the same toolchain and versions as `console/`). This Next.js has breaking changes from older versions: read the guides in `node_modules/next/dist/docs/` before writing Next code. |
| Workspace | `gateway/pnpm-workspace.yaml` (portal and `gateway/packages/*`), Node 24, pnpm 11.2.2. |

Local ports: API `8200`, portal `3100`. Never use 3000–3002, 8000, 8001 or 8100 (AequorOS).

## Data and copy

- Synthetic data only: fictional institutions and people (the design's names:
  Meridian Coast Bank, Harbourline Bank, Northgate Savings & Loans, Kofi Owusu, …).
  Never a real bank's or person's name. Real BoG return codes are fine.
- Demo seeding runs only when `GATEWAY_ENV` is `local`, `test` or `sandbox` and
  refuses `production`.
- UI copy is production copy: plain language, no raw enums or internal ids on
  screen except receipt ids, query numbers and return codes, which supervisors use.
- Facts BoG has not given us stay visible placeholders, exactly as the design shows
  them: `[BoG due-date rule]`, `[Retention period set by BoG]`, the dashed MARK logo.
  Never invent regulator policy.

## Design

`design/*.dc.html` is the approved design. Match its layout, hierarchy, copy,
colours (navy `#0F1E36` navigation, `#1F4FD1` actions, the status chip palette),
type (Inter, IBM Plex Mono for ids and digests) and accessibility (real buttons,
links and labelled inputs, WCAG AA contrast, 44 px targets, status never colour-only).
Screens: `Main` (overview), `SignIn`, `Inbox`, `Submission`, `Queries`,
`Institution`, `Calendar`, `Returns`, `Onboarding`, `Audit`, `Access`, and the
shared `Sidebar`.

## Done means verified

Every workstream reports, with command output: typecheck, lint, tests passing, and
the thing actually running (`api`: the server answers and the OpenAPI export is
fresh; `portal`: every screen renders against the API or its mock without console
errors). A test that cannot fail is not a test. If something is skipped, say so.
