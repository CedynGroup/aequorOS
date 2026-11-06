# Dashboard feature layout

The dashboard is moving from a layer-first tree (`app/` routes, `components/`,
`lib/`) to one directory per product feature, mirroring [the backend feature
layout](../../CODEBASE_CONVENTIONS.md#5-feature-layout). New code goes in the
target layout; existing code moves one feature per change, in a pure-rename
move PR.

## Target shape

- **`features/<feature>/`** holds everything a feature owns: its components
  (moved from `components/<feature>/`), feature lib code, and page bodies under
  `routes/`.
- **`tests/<feature>/`** holds that feature's tests; see the
  [testing guide](README.md#unit-test-collection) for suite suffixes and runner selection.
  Shared test helpers live in `tests/support/`.
- **Route pages stay under `app/`.** App Router requires `page.tsx` inside
  `app/`, so a moved page body lives at `features/<feature>/routes/<name>.tsx`
  and the route file becomes a thin re-export shim
  (`export { default } from "@/features/<feature>/routes/<name>"`). Shimmed
  moves leave the ledger untouched: they are framework structure, not rename
  history.
- **`features/<feature>/public.ts`** is the cross-feature interface:
  re-exports only, no logic. A feature imports another feature only through its
  `public` module, never its internals.
- **The shared layer** every feature may import: `lib/api/**` (generated
  client, query hooks, fail-closed value presentation), `components/ui/**`,
  `components/charts/**`, `components/shell/**` and other `lib` source modules
  not owned by a feature. Ownership of
  `lib/<name>/` and `components/<name>/` follows the feature of the same name
  once it exists; other component directories remain unowned legacy, and a
  feature importing one records a baseline violation. Shared code never imports
  a feature, not even through its public interface.
- `app/` is the composition root: route files and shims may import any feature.
  Features must not import `app/`.

The dashboard-wide ECharts entrance (`EChart.tsx`), deferred canvas
(`EChartCanvas.tsx`) and resolved theme (`echartsTheme.ts`) live in
`components/charts/`. Import the entrance and theme directly, not through BI's
public interface. Only the entrance loads the canvas, with
`dynamic(..., { ssr: false })`. The home deferred loader explicitly imports that
entrance to retain its two-stage manifest without eagerly importing the canvas.
`scripts/assert-home-route-bundle.mjs` enforces both deferred stages and keeps the
chart runtime out of the home entry graph.

## The ratchet

`scripts/check_feature_boundaries.mjs` scans `.ts`/`.tsx` files under `features/`
and the shared origins under `components/` and `lib/`. It compares cross-feature
private imports, feature-to-unowned imports, shared-to-feature imports and
feature-to-`app` imports against exact importer/target pairs in
`feature_boundary_baseline.json`. Unowned legacy components and feature-owned
code still outside `features/` are not newly governed origins:

- a violation that is not in the baseline fails CI;
- a baseline entry that no longer occurs also fails, so the baseline only
  shrinks; regenerate with `--update` and review that the diff only deletes
  entries. A move PR may add entries for imports that were internal to the
  moved code before it gained an owner; such additions are reviewed in the
  move PR and retire as the imported modules gain owners of their own.

The guard uses the installed TypeScript parser, without executing source.
Static imports, re-exports, literal dynamic imports, `require`, import-equals and
import-type expressions count, including type-only edges. It resolves `@/` and
relative paths to source files or directory indexes; comments, strings and JSX
prose are not dependencies. Missing or empty governed origin populations,
unreadable source, malformed syntax and malformed baselines fail closed,
including during `--update`. External packages, unresolved targets and nonliteral
runtime module names are outside this static scan.

The current exceptions live in `feature_boundary_baseline.json`: exact pairs,
not prefix exemptions or a total-count budget. Type-only exceptions represent
boundary debt without implying a runtime bundle edge. The generic chart move
retires the shared-chart-to-BI pairs; other exact exceptions remain independently
tracked. Retired pairs must not remain in the baseline; the guard rejects them
as stale.

The guard runs as the `Guard dashboard feature boundaries` CI step and is
wired into the `test:legacy` suite via `scripts/check_feature_boundaries.test.mjs`.

`scripts/check_public_interfaces.mjs` separately enforces the re-export-only
syntax contract using the installed TypeScript parser. Every
`features/**/public.ts` may contain comments, leading string directives, static
imports and export declarations (named, type-only, wildcard or namespace).
Module specifiers and import/export attribute values must be string literals.
Local definitions, variable declarations (even literal initializers), default
export expressions, calls and control flow are rejected. An established public
root disappearing fails coverage; new public modules are discovered recursively.
The guard never imports dependencies or changes declaration order. Its CLI
controls and current-tree check run in `test:legacy` through
`scripts/check_public_interfaces.test.mjs`.

## Moving a feature

A move PR runs the codemod from `backend/dashboard/`, then nothing else:

```bash
node scripts/feature_moves.mjs move components/fx features/fx/components
node scripts/feature_moves.mjs move --shim "app/(app)/fx/page.tsx" features/fx/routes/exposure.tsx
node scripts/feature_moves.mjs check
```

The codemod `git mv`s the files, records non-shim moves in
`feature_module_moves.json` (the cumulative ledger of `[old, new]` pairs), and
rewrites `@/` alias imports, relative imports and bare slash-path references in
tracked `.ts/.tsx/.js/.mjs/.json/.md/.yml/.css` files. It validates a batch
before changing files; overlapping moves run as separate commands. Review the
rewritten bare-path matches by hand: the bounded rewrite intentionally skips
references preceded by another path segment (`backend/dashboard/components/...`
doc links need a hand edit). A move must not change behavior: logic changes go
in separate PRs, and moved route pages keep their named exports (metadata,
route segment config) — extend the shim by hand when a page has any.

## Differences from the backend layout

- No Python type-check baseline; the gates are `tsc --noEmit`, ESLint, Vitest
  and the boundary guard.
- Next.js route shims under `app/` are permanent, so "a move leaves no
  compatibility shim at the old path" does not apply to route pages.
- The ledger is informational today (baseline identity is path-based); it
  exists so review can trace every move, matching
  [the backend ledger contract](../../CODEBASE_CONVENTIONS.md#5-feature-layout).
