/**
 * Every IRRBB Standardised Framework panel, rendered for real, against the
 * payloads that would break it.
 *
 * Same harness and the same reasoning as `components/icaap/p3/resilience.test.ts`
 * — read its header for why the components are transpiled and evaluated here
 * rather than imported. Each panel is rendered under:
 *
 *   (a) an EMPTY payload `{}` normalised — the shape an unfinished API sends;
 *   (a-raw) the same body UNNORMALISED — the belt to the normaliser's braces;
 *   (b) NULLED optional fields;
 *   (c) a 404 — no result for this reporting date, which must render the
 *       first-class empty state and NOT a red error;
 *   (d) 403 / 409 / 500 — genuine failures, which must still look like errors.
 *
 * Plus the rules that are specific to this screen and are the whole point of
 * it: no absent figure is drawn as a zero, a refusal replaces the figures, the
 * mandate never reassures on an unreadable payload, and the assumption tallies
 * survive to the screen.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import ts from "typescript";
import * as React from "react";
import { act, create } from "react-test-renderer";

const HERE = __dirname.includes(".test-out")
  ? resolve(__dirname, "../../../../components/irr/sf")
  : __dirname;

let failures = 0;
function test(name: string, fn: () => void): void {
  try {
    fn();
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${name}`);
    console.error(error);
  }
}

const el = React.createElement;
type Props = Record<string, unknown>;
type Renderable = Parameters<typeof React.createElement>[0];

// ---------------------------------------------------------------------------
// Stubs for everything that is not the component under test
// ---------------------------------------------------------------------------

/** The signed-in principal's IRRBB authority, as the shell would project it. */
const authority = { irrbbAggregatedView: true, irrbbRun: true };
function withAuthority(next: Partial<typeof authority>): void {
  Object.assign(authority, { irrbbAggregatedView: true, irrbbRun: true }, next);
}

const hookStubs: Record<string, () => unknown> = {};
const idleMutation = () => ({
  mutate: () => {},
  mutateAsync: async () => ({}),
  isPending: false,
  isError: false,
  error: null,
  reset: () => {},
});
const hooksModule = new Proxy({} as Record<string, unknown>, {
  get: (_t, property: string) =>
    property === "__esModule" ? true : (hookStubs[property] ?? idleMutation),
});

const passthrough = ({ children }: { children?: unknown }) =>
  el("div", null, children as never);

function stubFor(specifier: string): unknown | undefined {
  switch (specifier) {
    case "@/lib/api/irrbbSf":
      return hooksModule;
    case "@/components/shell/BankContext":
      // Authority is a MUTABLE fixture: the default grants everything so each
      // control renders, and the tests that care flip it to check that a
      // control the server would refuse is never offered.
      return {
        useBankContext: () => ({
          bank: { id: "BK-SAMP0001" },
          period: { id: "11111111-1111-4111-8111-111111111111" },
          moduleScope: { ...authority },
        }),
      };
    case "@/lib/modules":
      return {
        IRRBB_CONFIDENTIAL_RUN_REASON: "You need interest-rate run authority.",
      };
    case "@/lib/api/values":
      return { shortId: (value: string, length: number) => value.slice(0, length) };
    case "@/lib/format":
      // Deterministic, and deliberately NOT the real jurisdiction binding: this
      // suite is about resilience, not about formatting.
      return {
        fmtCurrency: (value: number) => `XXX ${value}`,
        fmtNum: (value: number) => String(value),
        fmtPct: (value: number) => `${value}%`,
        fmtLocale: () => "en-GB",
        regShort: () => "the regulator",
        currencyCode: () => "XXX",
      };
    case "@/components/ui/QueryBoundary":
      return {
        __esModule: true,
        default: ({ error, children }: { error?: unknown; children?: unknown }) =>
          error
            ? el("div", null, "QUERY_BOUNDARY_ERROR")
            : el("div", null, children as never),
        ErrorPanel: passthrough,
      };
    case "@/components/ui/SectionCard":
      return {
        __esModule: true,
        default: ({ title, subtitle, actions, children, footer }: Props) =>
          el(
            "section",
            null,
            el("h3", null, title as never),
            el("p", null, subtitle as never),
            actions as never,
            children as never,
            footer as never,
          ),
      };
    case "@/components/ui/PageHeader":
      return {
        __esModule: true,
        default: ({ eyebrow, title, subtitle, action }: Props) =>
          el(
            "header",
            null,
            el("p", null, eyebrow as never),
            el("h1", null, title as never),
            el("p", null, subtitle as never),
            action as never,
          ),
      };
    case "@/components/ui/PageContainer":
      return { __esModule: true, default: passthrough };
    case "@/components/ui/StatusPill":
      return { __esModule: true, default: passthrough };
    case "@/components/ui/DisabledWithReason":
      return {
        DisabledWithReason: ({ reason, children }: Props) =>
          el(
            "div",
            null,
            reason as never,
            (typeof children === "function"
              ? (children as (id: string) => unknown)("reason-id")
              : children) as never,
          ),
      };
    case "next/link":
      return ({ children, ...props }: { children?: unknown }) =>
        el("a", props as Props, children as never);
    case "lucide-react":
      return new Proxy({} as Record<string, unknown>, {
        get: () => () => el("span"),
      });
    default:
      return undefined;
  }
}

// ---------------------------------------------------------------------------
// The loader: transpile the REAL component source and evaluate it
// ---------------------------------------------------------------------------

const cache = new Map<string, Record<string, unknown>>();

function resolveLocal(from: string, specifier: string): string | null {
  if (!specifier.startsWith(".")) return null;
  const base = resolve(dirname(from), specifier);
  for (const candidate of [
    `${base}.tsx`,
    `${base}.ts`,
    join(base, "index.tsx"),
    join(base, "index.ts"),
  ]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

const APP_ROOT = resolve(HERE, "../../..");

function resolveAliased(specifier: string): string | null {
  const base = join(APP_ROOT, specifier.slice("@/".length));
  for (const candidate of [`${base}.tsx`, `${base}.ts`, join(base, "index.ts")]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function loadModule(file: string): Record<string, unknown> {
  const cached = cache.get(file);
  if (cached) return cached;

  const source = readFileSync(file, "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: file,
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
  });

  const moduleExports: Record<string, unknown> = {};
  const moduleObject = { exports: moduleExports };
  cache.set(file, moduleExports);

  const localRequire = (specifier: string): unknown => {
    const local = resolveLocal(file, specifier);
    if (local) return loadModule(local);
    const stub = stubFor(specifier);
    if (stub !== undefined) return stub;
    // An unstubbed `@/…` is one of OUR pure modules (the labels, the display
    // constants, the availability notice). Load the real one: stubbing it
    // would hide the very copy the panel is being tested for.
    if (specifier.startsWith("@/")) {
      const aliased = resolveAliased(specifier);
      if (aliased) return loadModule(aliased);
    }
    return require(specifier);
  };

  const factory = new Function(
    "exports",
    "require",
    "module",
    "__filename",
    "__dirname",
    outputText,
  ) as (
    exports: Record<string, unknown>,
    req: (s: string) => unknown,
    mod: { exports: Record<string, unknown> },
    filename: string,
    dir: string,
  ) => void;

  factory(moduleExports, localRequire, moduleObject, file, dirname(file));
  const settledExports = moduleObject.exports;
  cache.set(file, settledExports);
  return settledExports;
}

function panel(name: string): Renderable {
  return loadModule(join(HERE, name)).default as Renderable;
}

const normalize = loadModule(
  resolve(HERE, "../../../lib/api/irrbbSfNormalize.ts"),
) as Record<string, (value: unknown) => unknown>;

const labels = loadModule(join(HERE, "labels.ts")) as Record<string, string>;
const availability = loadModule(join(HERE, "availability.tsx")) as Record<
  string,
  string
>;

type SfView = ReturnType<typeof emptyView>;
function emptyView() {
  return normalize.normalizeStandardisedFramework({}) as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function settled(data: unknown) {
  return {
    data,
    isLoading: false,
    isPending: false,
    error: null,
    refetch: () => {},
  };
}

function failedWith(status: number) {
  const error = Object.assign(new Error(`Request failed (${status}).`), {
    name: "ApiError",
    status,
    code: null,
    errorCode: null,
  });
  return {
    data: undefined,
    isLoading: false,
    isPending: false,
    error,
    refetch: () => {},
  };
}

const READS = [
  "useIrrbbStandardisedFramework",
  "useIrrbbStandardisedFrameworkAttempts",
];

function stubReads(result: unknown, attempts: unknown = settled(undefined)): void {
  for (const key of Object.keys(hookStubs)) delete hookStubs[key];
  hookStubs.useIrrbbStandardisedFramework = () => result;
  hookStubs.useIrrbbStandardisedFrameworkAttempts = () => attempts;
  hookStubs.useRunIrrbbStandardisedFramework = idleMutation;
}

function textOf(node: unknown): string {
  if (node === null || node === undefined || node === false) return "";
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join(" ");
  return textOf((node as { children?: unknown }).children);
}

function render(target: Renderable, props: Props = {}): string {
  let tree: ReturnType<typeof create> | undefined;
  act(() => {
    tree = create(el(target, props));
  });
  const json = tree!.toJSON();
  act(() => tree!.unmount());
  return textOf(json);
}

const WORKSPACE = loadModule(
  resolve(HERE, "../StandardisedFramework.tsx"),
).default as Renderable;

/** Presentational panels, given their data as a prop. */
function presentational(): [string, Renderable, Props][] {
  const view = emptyView();
  return [
    ["MandateCard", panel("MandateCard.tsx"), { mandate: view.mandate }],
    [
      "RefusalCard",
      panel("RefusalCard.tsx"),
      { run: normalize.normalizeRunSummary({}) },
    ],
    ["MeasuresPanel", panel("MeasuresPanel.tsx"), { view }],
    ["ScenariosPanel", panel("ScenariosPanel.tsx"), { view }],
    ["BookPanel", panel("BookPanel.tsx"), { view }],
    ["AssumptionsPanel", panel("AssumptionsPanel.tsx"), { view }],
  ];
}

// ---------------------------------------------------------------------------
// (a) empty  (a-raw) unnormalised  (b) nulled
// ---------------------------------------------------------------------------

test("(a) every panel renders a normalised empty payload without throwing", () => {
  for (const [name, target, props] of presentational()) {
    assert.doesNotThrow(() => render(target, props), `${name} threw on {}`);
  }
  stubReads(settled(emptyView()), settled(normalize.normalizeAttempts({})));
  assert.doesNotThrow(() => render(WORKSPACE), "the workspace threw on {}");
});

test("(a-raw) the workspace survives a RAW empty payload, unnormalised", () => {
  stubReads(settled({}), settled({}));
  assert.doesNotThrow(
    () => render(WORKSPACE),
    "the workspace threw on an unnormalised {}",
  );
});

test("(a-raw) every presentational panel survives an unnormalised body", () => {
  const raw = {
    mandate: {},
    measures: {},
    parameters: null,
    scenarios: null,
    currencies: null,
    table8: null,
    nmdCategories: null,
    ladders: null,
    dataQuality: null,
    table7Quantitative: null,
  } as unknown as SfView;
  assert.doesNotThrow(() =>
    render(panel("MeasuresPanel.tsx"), {
      view: normalize.normalizeStandardisedFramework(raw),
    }),
  );
  assert.doesNotThrow(() =>
    render(panel("AssumptionsPanel.tsx"), {
      view: normalize.normalizeStandardisedFramework(raw),
    }),
  );
});

test("(b) nulled optional fields render without throwing", () => {
  const view = normalize.normalizeStandardisedFramework({
    mandate: { statement: null, confirmationStatus: null },
    tier1: null,
    pctTier1: null,
    outlierThresholdPct: null,
    automaticOptionStatement: null,
    postShockFloorStatement: null,
    scenarios: [{ code: "x", label: null, loss: null, net: null }],
    currencies: [{ currency: "USD", curveName: null, curveAsOf: null }],
    nmdCategories: [{ currency: "USD", category: "retail", label: null }],
    ladders: [{ currency: "USD", bucketKey: "b", bucketLabel: null }],
    table8: [{ code: "x", label: null }],
    dataQuality: { assumptions: [{ marker: "m", label: null }] },
  });
  for (const [name, target] of [
    ["MeasuresPanel", panel("MeasuresPanel.tsx")],
    ["ScenariosPanel", panel("ScenariosPanel.tsx")],
    ["BookPanel", panel("BookPanel.tsx")],
    ["AssumptionsPanel", panel("AssumptionsPanel.tsx")],
  ] as [string, Renderable][]) {
    assert.doesNotThrow(() => render(target, { view }), `${name} threw`);
  }
});

// ---------------------------------------------------------------------------
// (c) 404 is an empty state  (d) everything else is an error
// ---------------------------------------------------------------------------

test("(c) a 404 renders the no-result sentence and NOT an error panel", () => {
  stubReads(failedWith(404), settled(normalize.normalizeAttempts({})));
  const text = render(WORKSPACE);
  assert.ok(
    text.includes(availability.SF_NO_RESULT),
    "the no-result sentence is missing",
  );
  assert.ok(
    !text.includes("QUERY_BOUNDARY_ERROR"),
    "a 404 rendered as a failure",
  );
  // And it does not print the server's own 404 sentence, which can be the
  // deny-hide.
  assert.ok(!text.includes("Bank not found"));
});

test("(c) 405 and 501 render the not-enabled sentence, not an error", () => {
  for (const status of [405, 501]) {
    stubReads(failedWith(status), settled(normalize.normalizeAttempts({})));
    const text = render(WORKSPACE);
    assert.ok(
      text.includes(availability.SF_NOT_ENABLED),
      `${status} did not render the not-enabled sentence`,
    );
    assert.ok(!text.includes("QUERY_BOUNDARY_ERROR"), `${status} looked like a failure`);
  }
});

test("(d) a genuine failure still looks like a failure", () => {
  for (const status of [403, 409, 500]) {
    stubReads(failedWith(status), settled(normalize.normalizeAttempts({})));
    const text = render(WORKSPACE);
    assert.ok(
      text.includes("QUERY_BOUNDARY_ERROR"),
      `${status} was swallowed as an empty state`,
    );
  }
});

// ---------------------------------------------------------------------------
// The rules this screen exists for
// ---------------------------------------------------------------------------

test("no absent figure is drawn as a zero", () => {
  const view = emptyView();
  for (const [name, target] of [
    ["MeasuresPanel", panel("MeasuresPanel.tsx")],
    ["ScenariosPanel", panel("ScenariosPanel.tsx")],
    ["BookPanel", panel("BookPanel.tsx")],
  ] as [string, Renderable][]) {
    const text = render(target, { view });
    assert.ok(
      !/\b0(\.0+)?\b/.test(text),
      `${name} rendered a zero for an absent figure: ${text}`,
    );
  }
  assert.ok(render(panel("MeasuresPanel.tsx"), { view }).includes(labels.NOT_REPORTED));
});

test("an unassessable outlier test is stated, not silently passed", () => {
  const text = render(panel("MeasuresPanel.tsx"), { view: emptyView() });
  assert.ok(text.includes(labels.OUTLIER_NOT_ASSESSABLE));
  assert.ok(!text.includes(labels.OUTLIER_BELOW));
});

test("a refusal replaces the figures and names the next step", () => {
  const refusedRun = normalize.normalizeRunSummary({
    id: "r2",
    status: "failed",
    error: { code: "irrbb_sf_options_unsupported" },
  });
  const text = render(panel("RefusalCard.tsx"), { run: refusedRun });
  assert.ok(/option/i.test(text));
  assert.ok(text.includes(labels.REFUSAL_NEXT_STEP));
  assert.ok(!text.includes("irrbb_sf_options_unsupported"));
  assert.ok(!/\b0(\.0+)?\b/.test(text));
});

test("a refused attempt reaches the workspace even with no result at all", () => {
  stubReads(
    failedWith(404),
    settled(
      normalize.normalizeAttempts({
        runs: [
          {
            id: "r2",
            status: "failed",
            error: { code: "irrbb_sf_options_unsupported" },
          },
        ],
      }),
    ),
  );
  const text = render(WORKSPACE);
  assert.ok(text.includes(labels.REFUSAL_HEADING), "the refusal was not shown");
  assert.ok(/option/i.test(text));
});

test("a refusal NEWER than the served result is shown beside it", () => {
  const view = emptyView();
  stubReads(
    settled(view),
    settled(
      normalize.normalizeAttempts({
        runs: [
          {
            id: "r9",
            status: "failed",
            error: { code: "irrbb_sf_options_unsupported" },
          },
          { id: "r1", status: "succeeded" },
        ],
      }),
    ),
  );
  const text = render(WORKSPACE);
  assert.ok(
    text.includes(labels.REFUSAL_HEADING),
    "figures were served with a newer refusal hidden",
  );
});

test("an unreadable mandate never reassures", () => {
  const text = render(panel("MandateCard.tsx"), {
    mandate: (emptyView() as { mandate: unknown }).mandate,
  });
  assert.ok(text.includes(labels.MANDATE_UNREADABLE));
  assert.ok(!text.includes(labels.INTERIM_STILL_VALID));
});

test("a pending commencement date carries its qualifier on screen", () => {
  const mandate = normalize.normalizeMandate({
    statement: "It becomes mandatory later.",
    confirmationStatus: "pending",
  });
  const text = render(panel("MandateCard.tsx"), { mandate });
  assert.ok(text.includes(labels.MANDATE_PENDING));
  assert.ok(text.includes(labels.PENDING_CHIP));
});

test("assumption tallies reach the screen with their counts", () => {
  const view = normalize.normalizeStandardisedFramework({
    dataQuality: {
      assumptions: [
        { marker: "a", label: "Default cash-flow profile", count: 40 },
        { marker: "b", label: "Default reset frequency", count: 1 },
      ],
    },
  });
  const text = render(panel("AssumptionsPanel.tsx"), { view });
  assert.ok(text.includes("Default cash-flow profile"));
  assert.ok(text.includes("40"), "the tally was flattened away");
  assert.ok(text.includes("Default reset frequency"));
  assert.ok(text.includes("1"), "the second tally was dropped");
});

test("an unreported tally says so rather than printing a zero", () => {
  const view = normalize.normalizeStandardisedFramework({
    dataQuality: { assumptions: [{ marker: "a", label: "A default" }] },
  });
  const text = render(panel("AssumptionsPanel.tsx"), { view });
  assert.ok(text.includes(labels.COUNT_NOT_REPORTED));
});

test("a representative parameter is labelled as such on its own row", () => {
  const view = normalize.normalizeStandardisedFramework({
    parameters: [
      {
        code: "irrbb_sf_default_cash_flow_profile",
        label: "Default cash-flow profile",
        value: "bullet",
        representative: true,
        pendingConfirmation: true,
        statement: "Representative only — not a published supervisory value.",
      },
    ],
  });
  const text = render(panel("AssumptionsPanel.tsx"), { view });
  assert.ok(text.includes(labels.REPRESENTATIVE_CHIP));
  assert.ok(text.includes(labels.PENDING_CHIP));
  assert.ok(
    text.includes("Representative only — not a published supervisory value."),
  );
  assert.ok(!text.includes("irrbb_sf_default_cash_flow_profile"));
});

test("the workspace states that these figures are not filed", () => {
  stubReads(settled(emptyView()), settled(normalize.normalizeAttempts({})));
  const text = render(WORKSPACE);
  assert.ok(text.includes(labels.SUPERVISORY_MONITORING));
});

test("without run authority the reason is shown instead of a bare control", () => {
  withAuthority({ irrbbRun: false });
  stubReads(settled(emptyView()), settled(normalize.normalizeAttempts({})));
  const text = render(WORKSPACE);
  assert.ok(
    text.includes("You need interest-rate run authority."),
    "the disabled control gave no reason",
  );
  withAuthority({});
});

test("without view authority the screen says so and reads nothing", () => {
  withAuthority({ irrbbAggregatedView: false });
  stubReads(settled(undefined), settled(undefined));
  const text = render(WORKSPACE);
  assert.ok(text.includes(labels.VIEW_NEEDS_AUTHORITY));
  assert.ok(!text.includes("QUERY_BOUNDARY_ERROR"));
  withAuthority({});
});

// ---------------------------------------------------------------------------
// The post-shock floor (GAP-4 item 4, second half)
// ---------------------------------------------------------------------------
//
// No post-shock rate floor is built, deliberately: the magnitudes have no
// source this platform holds. The refusal to invent one is only honest if the
// PRODUCT says so, and the backend suites pin the sentence on the payload
// while nothing pinned that it reaches a reader. This does.

test("the screen states that no post-shock floor was applied, and calls it open", () => {
  const statement =
    "No post-shock rate floor was applied: shocked rates are reported exactly " +
    "as the prescribed shock shapes produce them, including where a shape " +
    "takes a rate below zero. The guideline extract these shapes were " +
    "transcribed from carries no floor provision, while the international " +
    "standardised framework they follow does prescribe one, so the absence is " +
    "recorded as an open point for the supervisor rather than a settled " +
    "reading. It binds only where a downward shock would take a low starting " +
    "rate below zero.";
  stubReads(
    settled(
      normalize.normalizeStandardisedFramework({
        postShockFloorStatement: statement,
      }),
    ),
    settled(normalize.normalizeAttempts({})),
  );
  const text = render(WORKSPACE);
  assert.ok(text.includes(statement), "the floor sentence never reached a reader");
  // The heading must not imply a floor was applied.
  assert.ok(!/floor applied|floored at/i.test(text));
});

test("with no floor sentence the screen asserts nothing about a floor", () => {
  stubReads(settled(emptyView()), settled(normalize.normalizeAttempts({})));
  const text = render(WORKSPACE);
  assert.ok(!/post-shock rate floor/i.test(text));
});

test("every read this screen makes is stubbed, so none is silently missed", () => {
  const workspaceSource = readFileSync(
    resolve(HERE, "../StandardisedFramework.tsx"),
    "utf8",
  );
  for (const hook of READS) {
    assert.ok(
      workspaceSource.includes(hook),
      `${hook} is stubbed but no longer used — the stub is stale`,
    );
  }
});

if (failures > 0) {
  console.error(
    `${failures} IRRBB standardised framework resilience test(s) failed`,
  );
  process.exit(1);
}
console.log("IRRBB standardised framework resilience: all checks passed");
