---
name: verification
description: Prove a change works in the running product and record evidence, on any aequorOS surface (tenant API, operator API, bank dashboard, staff console, marketing site). Use when a change alters user-visible behavior, an API response or contract, or when asked to verify, screenshot, demo or confirm a change in the real app rather than only in tests.
---

# Verification

One workflow for proving a change on a running aequorOS surface. This file owns
the shared rules; each surface has a short reference that says only how to reach
it and capture on it:

| Surface                       | Reference                                                    |
| ----------------------------- | ------------------------------------------------------------ |
| Tenant API (`backend`, :8000) | [references/tenant-api.md](references/tenant-api.md)         |
| Staff operator API (:8100)    | [references/operator-api.md](references/operator-api.md)     |
| Bank dashboard                | [references/dashboard.md](references/dashboard.md)           |
| Staff operator console        | [references/console.md](references/console.md)               |
| Marketing site                | [references/marketing-site.md](references/marketing-site.md) |

Tools, fixtures and feature maps are ordinary repo code and docs. The references
link to them; this skill does not copy them. Tests, gates and CI are not
replaced by verification: run the gates in
[ARCHITECTURE.md §8](../../../ARCHITECTURE.md#8-validation-commands) as well.

## When verification applies

Verify on the running surface when a change:

- alters what a person sees or can do in the dashboard, console or marketing site;
- alters an API route, response, status code, error envelope or the generated
  client contract;
- alters what a worker, live-plane or official-run result produces for a bank.

A change with none of these (refactor, test-only, docs, typing, tooling) has no
user-visible behavior to prove. Say so and list the checks that showed it:
`no user-visible change - screenshots not applicable`.

## Workflow

1. **Pick the surfaces** the change reaches, from the table above. A backend
   change that feeds a dashboard figure touches both.
2. **Write the case matrix** (below) before starting anything. Keep only the rows
   the change touches.
3. **Check out the head you intend to ship**, commit it, and keep the tree clean.
   Evidence claims a commit; a dirty tree makes the claim unverifiable.
4. **Restart long-lived processes** the change touches. The standalone worker has
   no `--reload` and computes with whatever code it holds
   ([AGENTS.md](../../../AGENTS.md#hard-invariants)).
5. **Reach the surface** using its reference, with local test data only.
6. **Capture before and after** for a UI change: the base commit first, then the
   head. For an API change capture the request and the response for each case.
7. **Record evidence** in one manifest (below), outside the worktree.
8. **Hand off** with the rules below.

## Case matrix

List one row per case the change touches, then verify each. Draw cases from these
axes; add others the change introduces.

| Axis             | Cases worth a row                                                                                                                                 |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Authority        | permitted principal; principal lacking the grant (refusal is the expected result); a sibling bank in the same organization (RLS tenant is shared) |
| Data state       | populated; empty ("no computed data yet"); figure unavailable or not applicable; refused or failed computation                                    |
| Freshness        | live result current; stale or recomputing; official run versus live view                                                                          |
| Rendering        | supported themes; desktop and a narrow viewport                                                                                                   |
| Input            | valid; invalid (validation error shape); boundary values the change introduced                                                                    |
| Contract         | status code, error envelope, generated-client freshness for API changes                                                                           |
| Regression guard | the neighbouring screen or route that shares the changed component or service                                                                     |

Product traps the matrix should catch, because they are invariants:

- An unavailable figure is shown as unavailable, never as zero or a plausible
  number. Verify the unavailable case, not only the happy one.
- Jurisdiction, currency, locale and regulator come from tenant data. Verify a
  change against what the bank's data says, not a literal.
- A refusal on an official path is the fail-closed design. Record it as a result,
  not as a fault to work around.
- Tenant health is read from what the platform computed (live summary, freshness,
  alerts). Never derive facts yourself to make a screen populate.

Write the matrix as a table in the PR description or a file separate from the manifest:

```text
case                         surface     principal        data state   expected
---------------------------  ----------  ---------------  -----------  ---------------------------
liquidity ratio, unavailable dashboard   analyst fixture  no book      "Unavailable", no zero, no bar
sibling bank refused         tenant API  analyst fixture  populated    404 or 403 envelope, no body leak
```

## Local test data only

- Sign in only with accounts and tenants that exist in a disposable local
  environment: the hermetic end-to-end stack's minted fixture identities, the
  provisioned local tenant administrator or a local member with the scoped
  authority required by the case, a local operator password account, or the
  local-only operator dev token. Each reference names which applies.
- Bank financial data enters only through the Data Engine (upload, adapter, API
  push) from synthetic files. There is no seed route; never add one to make a
  screen populate. The hermetic stack's fixture bank is the default.
- Never use production or staging hostnames, real bank or customer data,
  tokens or keys from any deployed environment. This repository is public: the
  skill, manifests, captions and screenshots must contain only synthetic
  identifiers and placeholders such as `BK-XXXXXXXX`, `OR-XXXXXXXX`,
  `analyst@example.test`.
- Pass secrets by environment variable. Keep credentials and real identifiers
  out of evidence, including commands, screenshots and captions. Discard any
  screenshot that exposes them.

## Evidence record

Keep one tab-separated manifest per verification run outside the worktree, with
one row per artifact and the five columns below.

| Field         | Meaning                                                                                         |
| ------------- | ----------------------------------------------------------------------------------------------- |
| `filename`    | artifact name relative to the manifest: `<surface>-<case>-<before\|after>.png`, `.txt`, `.json` |
| `case`        | the matrix row it proves, verbatim                                                              |
| `caption`     | one sentence: what the artifact shows and the expected result, with no secrets                  |
| `sha256`      | the digest only: the first field of `sha256sum <file>`, without the filename                    |
| `head_commit` | full `git rev-parse HEAD` of the checkout the surface ran from, taken at capture time           |

Non-visual evidence (a request and response pair, a command's output) is a text
or JSON file in the same directory and the same manifest. Capture from the local
fixture only. For API evidence, review the response locally and prepare the
sanitized request and response before writing the artifact. Keep the method,
sanitized URL, response status and relevant fields. Check both directions,
including headers, bodies and URL parameters, for credentials and real
identifiers. Replace sensitive values with placeholders, including bearer
tokens, cookies, access and refresh tokens, passwords and keys. Persist only
the sanitized result.

## Handoff

- Evidence never enters the repository. Keep every artifact and the manifest
  outside the worktree; remove any file the run created inside it (the
  dashboard's `e2e/.tmp` is ignored but still lives in the worktree, so point
  `E2E_RUNTIME_DIR` elsewhere). Leave `git status` clean.
- In the pull request, follow the
  [description template](../../../.github/PULL_REQUEST_TEMPLATE.md): list the
  screenshots under **Screenshots** by manifest row, and state in **How it was
  tested** which cases ran, which did not, and why. Evidence for a case that was
  not executed is a limit to disclose, never something to approximate or invent.
- Report outcomes as observed. A refusal, an unavailable state or a failing case
  is a result; say it plainly with the artifact that shows it.
- Stop every service the run started and restore anything the dev servers
  rewrite (the console dev server rewrites the tracked `console/next-env.d.ts`).
- Do not point mutating checks at a shared or primary database; use disposable
  databases only ([backend/README.md](../../../backend/README.md#test-databases-and-the-primary-database)).
