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
  `components/charts/**` and root-level `lib/*.ts` modules. Ownership of
  `lib/<name>/` and `components/<name>/` follows the feature of the same name
  once it exists; until then those modules are unowned legacy, and a feature
  importing one records a baseline violation.
- `app/` is the composition root: route files and shims may import any feature.

## The ratchet

`scripts/check_feature_boundaries.mjs` scans files under `features/` and
compares every cross-feature or unowned import against
`feature_boundary_baseline.json`:

- a violation that is not in the baseline fails CI;
- a baseline entry that no longer occurs also fails, so the baseline only
  shrinks; regenerate with `--update` and review that the diff only deletes
  entries. A move PR may add entries for imports that were internal to the
  moved code before it gained an owner; such additions are reviewed in the
  move PR and retire as the imported modules gain owners of their own.

The guard runs as the `Guard dashboard feature boundaries` CI step and is
wired into the `test:legacy` suite via `scripts/check_feature_boundaries.test.mjs`.

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
