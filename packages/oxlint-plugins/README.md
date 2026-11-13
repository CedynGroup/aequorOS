# @aequoros/oxlint-plugins

The `acts` oxlint plugin: the ACTS test-hygiene rules that keep AequorOS tests
deterministic. One plugin, three rules; every message starts with its ACTS code
so a finding can be cited in code review and suppressions without ambiguity.

| Code    | Rule id                     | Flags                                                                                    |
| ------- | --------------------------- | ---------------------------------------------------------------------------------------- |
| ACTS001 | `acts/require-frozen-time`  | `new Date()` / `Date.now()` in tests unless time is frozen (fake timers or `page.clock`) |
| ACTS002 | `acts/no-weak-test-awaits`  | `await new Promise((r) => setTimeout(r, n))` (incl. aliased helpers) and bare `await Promise.resolve()` |
| ACTS003 | `acts/prefer-e2e-stability` | `page.waitForTimeout(...)` and `page.waitForLoadState("networkidle")` in E2E tests       |

## Enabling the rules

Load the plugin through `jsPlugins` (paths resolve relative to the config file)
and enable each rule with an override scoped to test files. The rules assume
they only see test files; do not enable them for application code.

```jsonc
// <package>/.oxlintrc.json
{
  "$schema": "https://oxc.rs/oxlint/schema.json",
  "plugins": [],
  "jsPlugins": ["../../packages/oxlint-plugins/index.js"],
  "categories": {},
  "overrides": [
    {
      "files": ["tests/**/*.spec.{ts,tsx}", "tests/**/*.e2e.spec.ts"],
      "rules": {
        "acts/require-frozen-time": "error",
        "acts/no-weak-test-awaits": "error",
        "acts/prefer-e2e-stability": "error"
      }
    }
  ]
}
```

Override keys are `files` and `excludeFiles` (oxlint 1.92.0 rejects `includes`).

`oxlint` is pinned exactly in this package's `devDependencies`; consuming
packages lint through this package's pinned binary (`pnpm --filter
@aequoros/oxlint-plugins exec oxlint ...`) or their own identical pin.

## What counts as frozen time (ACTS001)

A read is accepted when any of these runs before it:

- `vi.useFakeTimers()` / `vi.setSystemTime()` (jest equivalents too) inside the
  same test, or inside a `beforeEach`/`beforeAll` enclosing it — including a
  top-level hook, which freezes the whole file;
- node:test's timer mock `mock.timers.enable(...)` (as `t.mock.timers.enable`)
  in the same test or an enclosing hook, when the mocked apis include `Date`
  (the default does; an explicit `apis` list without `Date` does not);
- `page.clock.install()` / `page.clock.setFixedTime()` (Playwright, any
  `*.clock` receiver) in the same places.

`new Date(<args>)` with explicit arguments is always allowed. The check is
per-callback: a freeze in one test does not silence a sibling test.

## Suppressing a finding

Suppression must carry a reason and a tracking issue, after the `--`
separator:

```ts
// oxlint-disable-next-line acts/no-weak-test-awaits -- must outlive the 1s idle-in-transaction timeout (#847)
await new Promise((r) => setTimeout(r, 1500));
```

`oxlint` accepts both `/* oxlint-disable ... */` block forms and
`eslint-disable-*` comments with the same rule ids.

## Tests

`pnpm --filter @aequoros/oxlint-plugins test` runs `node --test test/`. Every
case writes a fixture plus a throwaway `.oxlintrc.json` and executes the real
pinned `oxlint` binary (positive, negative, fake-timer and `page.clock`
scopes, and reason-carrying disable comments). The plugin runs on the ESLint
v9-compatible rule API that oxlint's `jsPlugins` support; scoping to test files
and per-package overrides live in the consuming packages' `.oxlintrc.json`.
