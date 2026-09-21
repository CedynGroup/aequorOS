/**
 * Every P2 panel, rendered for real, against the payloads that broke it.
 *
 * The founder's walkthrough hit an unhandled `TypeError: Cannot read properties
 * of undefined (reading 'length')` on the risks tab, because the component
 * trusted its declared contract on a backend that does not serve these routes
 * yet. A type check cannot catch that — the compiler believed the contract too.
 * So each component is RENDERED under three conditions:
 *
 *   (a) an EMPTY payload `{}` — the shape an API that is not finished sends;
 *   (b) NULLED optional fields — `parameters: null`, `risks: null`, …;
 *   (c) a 404 from the query — the routes do not exist on this deployment.
 *
 * Nothing may throw, and (c) must render the "not available yet" panel rather
 * than a red error or a blank screen. These fail without the fix: reading
 * `.length` on an absent field throws exactly the walkthrough's TypeError.
 *
 * WHY THIS FILE COMPILES ITS OWN SOURCES. The components are transpiled and
 * evaluated at RUNTIME, through the small loader below, rather than imported.
 * Importing them would pull the whole app's type graph into
 * `tsconfig.test.json`, and that graph does not currently type-check on a cold
 * build (338 pre-existing errors from the regenerated client's nominal alias
 * types, in files no ICAAP workstream owns — see `P2-FE.md`). The loader keeps
 * this suite honest about the components it tests and silent about everything
 * else. It transpiles only; type checking is `pnpm typecheck`'s job.
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
  ? resolve(__dirname, "../../../../components/icaap/p2")
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

/** The hook surface the panels call. Rewritten per case by `stubRead`. */
const hookStubs: Record<string, () => unknown> = {};
const idleMutation = () => ({
  mutate: () => {},
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

/** A settled query result, for the stubs declared above `settled` itself. */
function settledStub(data: unknown) {
  return { data, isLoading: false, isPending: false, error: null, refetch: () => {} };
}

function stubFor(specifier: string): unknown | undefined {
  switch (specifier) {
    case "@/lib/api/icaapRiskCapital":
      return hooksModule;
    case "@/components/shell/BankContext":
      // Full authority, so every control renders and every branch is exercised.
      return {
        useModuleScope: () => ({
          capitalEdit: true,
          capitalApprove: true,
          auditCreate: true,
        }),
        useBankContext: () => ({ bank: { id: "BK-SAMP0001" } }),
      };
    case "@/lib/api/client":
      return {
        isApiError: (value: unknown) =>
          Boolean(value) && (value as { name?: string }).name === "ApiError",
        isModuleUnavailable: (value: unknown) =>
          Boolean(value) &&
          (value as { name?: string }).name === "ModuleUnavailableError",
      };
    case "@/components/ui/QueryBoundary":
      // A faithful mini-boundary: children when settled, a marker on error, so
      // the test can tell "error panel" from "not available yet".
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
    case "@/components/ui/EmptyState":
      return {
        __esModule: true,
        default: ({ title, description }: Props) =>
          el("div", null, title as never, " ", description as never),
      };
    case "@/components/ui/StatusPill":
      return { __esModule: true, default: passthrough };
    case "@/components/icaap/Dialog":
      return {
        __esModule: true,
        default: ({ title, children, footer }: Props) =>
          el("div", null, title as never, children as never, footer as never),
        PrimaryButton: passthrough,
        SecondaryButton: passthrough,
        FieldLabel: passthrough,
      };
    case "@/components/icaap/Notices":
      return { RestrictedWidget: passthrough };
    // The stress and capital-plan tab REUSES the platform's existing panels.
    // They are stubbed because this suite is about the ICAAP panel's own
    // resilience, not about re-testing the stress module; what matters is that
    // the ICAAP panel decides correctly whether to render them at all.
    case "@/components/stress/AppendixIITables":
    case "@/components/stress/ManagementActionsPanel":
    case "@/components/stress/SignoffPanel":
    case "@/components/basel/CapitalPlanProjection":
      return {
        __esModule: true,
        default: () => el("div", null, "REUSED_PANEL"),
        CapitalPlanProjectionUnavailable: () =>
          el("div", null, "REUSED_PANEL"),
      };
    case "@/components/stress/hooks":
      return { useEnterpriseStressRun: () => settledStub(undefined) };
    case "@/lib/api/hooks":
      return { useCapitalPlan: () => settledStub(undefined) };
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

/** The dashboard package root, which `@/` points at. */
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
    // An unstubbed `@/…` is one of OUR pure modules (the appetite mirror, the
    // formatters). Load the real one: stubbing it would hide the very logic the
    // panel is being tested against.
    if (specifier.startsWith("@/")) {
      const aliased = resolveAliased(specifier);
      if (aliased) return loadModule(aliased);
    }
    // react, react/jsx-runtime and node builtins come from the real resolver.
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

function component(name: string): Renderable {
  const loaded = loadModule(join(HERE, name));
  return loaded.default as Renderable;
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
  "useIcaapRisks",
  "useIcaapAppetite",
  "useIcaapPillar2Register",
  "useIcaapTable5",
  "useIcaapReconciliation",
  "useIcaapAuditReviews",
  "useIcaapChallenges",
  "useIcaapCapitalTriggers",
  "useIcaapCapitalAllocation",
  "useIcaapParameterRegister",
  "useIcaapPillar2ItemRevisions",
  "useIcaapSupervisoryAddons",
  "useIcaapStressEvidence",
];

/** Point one read at `result` and settle every other read empty. */
function stubRead(hook: string, result: unknown): void {
  for (const key of Object.keys(hookStubs)) delete hookStubs[key];
  for (const name of READS) {
    hookStubs[name] = name === hook ? () => result : () => settled(undefined);
  }
}

/** Every string rendered anywhere in the tree, flattened. */
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

const normalize = loadModule(
  resolve(HERE, "../../../lib/api/icaapRiskCapitalNormalize.ts"),
) as Record<string, (value: unknown) => unknown>;
const availability = loadModule(join(HERE, "availability.tsx")) as {
  NOT_AVAILABLE_YET: string;
  p2UnavailableNotice: (error: unknown) => string | null;
};

const PANELS: [string, Renderable, string, (v: unknown) => unknown, Props?][] = [
  ["RiskRegister", component("RiskRegister.tsx"), "useIcaapRisks", normalize.normalizeRiskRegister],
  ["RiskAppetite", component("RiskAppetite.tsx"), "useIcaapAppetite", normalize.normalizeAppetite],
  [
    "Pillar2Register",
    component("Pillar2Register.tsx"),
    "useIcaapPillar2Register",
    normalize.normalizePillar2Register,
  ],
  [
    "CapitalReconciliation",
    component("CapitalReconciliation.tsx"),
    "useIcaapReconciliation",
    normalize.normalizeReconciliation,
  ],
  [
    "AuditReview",
    component("AuditReview.tsx"),
    "useIcaapAuditReviews",
    normalize.normalizeAuditReviews,
  ],
  [
    "ChallengeLog",
    component("ChallengeLog.tsx"),
    "useIcaapChallenges",
    normalize.normalizeChallenges,
  ],
  [
    "CapitalTriggers",
    component("CapitalTriggers.tsx"),
    "useIcaapCapitalTriggers",
    normalize.normalizeCapitalTriggers,
  ],
  [
    "CapitalAllocation",
    component("CapitalAllocation.tsx"),
    "useIcaapCapitalAllocation",
    normalize.normalizeAllocation,
  ],
  [
    "ParameterRegister",
    component("ParameterRegister.tsx"),
    "useIcaapParameterRegister",
    normalize.normalizeParameterRegister,
  ],
  [
    "SupervisoryAddons",
    component("SupervisoryAddons.tsx"),
    "useIcaapSupervisoryAddons",
    normalize.normalizeSupervisoryAddons,
  ],
  [
    "StressCapitalPlan",
    component("StressCapitalPlan.tsx"),
    "useIcaapStressEvidence",
    normalize.normalizeStressEvidence,
  ],
  [
    "ItemRevisions",
    component("ItemRevisions.tsx"),
    "useIcaapPillar2ItemRevisions",
    normalize.normalizePillar2Revisions,
    // It is mounted from a register row, so it needs the item it is about.
    { itemId: "22222222-2222-4222-8222-222222222222" },
  ],
];

const PROPS = {
  bankId: "BK-SAMP0001",
  cycleId: "11111111-1111-4111-8111-111111111111",
};

// ---------------------------------------------------------------------------
// (a) empty payload  (b) nulled optional fields
// ---------------------------------------------------------------------------

test("(a) every panel renders a normalised empty payload without throwing", () => {
  for (const [name, target, hook, normalizer, extra] of PANELS) {
    stubRead(hook, settled(normalizer({})));
    assert.doesNotThrow(
      () => render(target, { ...PROPS, ...extra }),
      `${name} threw on {}`,
    );
  }
});

test("(a-raw) every panel survives a RAW empty payload, unnormalised", () => {
  // The belt to the normaliser's braces: if a future hook forgets to normalise,
  // the component itself must still not crash.
  for (const [name, target, hook, , extra] of PANELS) {
    stubRead(hook, settled({}));
    assert.doesNotThrow(
      () => render(target, { ...PROPS, ...extra }),
      `${name} threw on raw {}`,
    );
  }
});

test("(b) every panel survives null where a list or object was declared", () => {
  const nulled = {
    risks: null,
    parameters: null,
    matrix: null,
    metrics: null,
    catalogue: null,
    summary: null,
    items: null,
    consistency: null,
    components: null,
    table5Totals: null,
    requirement: null,
    resources: null,
    controls: null,
    reviews: null,
    challenges: null,
    forums: null,
    columns: null,
    rows: null,
    units: null,
    cells: null,
    lines: null,
    results: null,
    floors: null,
    findings: null,
    revisions: null,
    addons: null,
    blocks: null,
    missing: null,
    firstCrossing: null,
    points: null,
  };
  for (const [name, target, hook, , extra] of PANELS) {
    stubRead(hook, settled(nulled));
    assert.doesNotThrow(
      () => render(target, { ...PROPS, ...extra }),
      `${name} threw on nulled fields`,
    );
  }
});

test("THE CRASH: the risks tab renders with `parameters` absent", () => {
  // The exact walkthrough payload: a body with no `parameters` key at all.
  stubRead("useIcaapRisks", settled({ cycleId: "c", asOf: "2025-12-31", risks: [] }));
  const [, riskRegister] = PANELS[0];
  assert.doesNotThrow(() => render(riskRegister, PROPS));
});

// ---------------------------------------------------------------------------
// (c) a 404 — the routes are not served here
// ---------------------------------------------------------------------------

test("(c) a 404 renders the not-available panel, not an error and not a blank", () => {
  for (const [name, target, hook, , extra] of PANELS) {
    stubRead(hook, failedWith(404));
    const text = render(target, { ...PROPS, ...extra });
    assert.match(text, /not available for this institution yet/, name);
    assert.ok(
      !text.includes("QUERY_BOUNDARY_ERROR"),
      `${name} showed an error panel for a route that simply does not exist`,
    );
    assert.ok(text.trim().length > 0, `${name} rendered a blank screen`);
  }
});

test("(c) 405 and 501 are treated the same way", () => {
  const [, riskRegister] = PANELS[0];
  for (const status of [405, 501]) {
    stubRead("useIcaapRisks", failedWith(status));
    assert.match(
      render(riskRegister, PROPS),
      /not available for this institution yet/,
      String(status),
    );
  }
});

test("a REAL failure still looks like a failure", () => {
  // 403, 409 and 500 are errors and must not be dressed up as "coming soon".
  const [, riskRegister] = PANELS[0];
  for (const status of [403, 409, 500]) {
    stubRead("useIcaapRisks", failedWith(status));
    const text = render(riskRegister, PROPS);
    assert.ok(text.includes("QUERY_BOUNDARY_ERROR"), String(status));
    assert.ok(!text.includes("not available for this institution yet"), String(status));
  }
});

test("the availability decision is total and never itself throws", () => {
  for (const value of [null, undefined, 0, "", "404", {}, [], new Error("boom")]) {
    assert.doesNotThrow(() => availability.p2UnavailableNotice(value));
    assert.equal(availability.p2UnavailableNotice(value), null, String(value));
  }
  const moduleUnavailable = Object.assign(new Error("no data"), {
    name: "ModuleUnavailableError",
    reason: "No computed position for this date yet.",
  });
  assert.equal(
    availability.p2UnavailableNotice(moduleUnavailable),
    "No computed position for this date yet.",
  );
  assert.equal(
    availability.p2UnavailableNotice(
      Object.assign(new Error(""), { name: "ModuleUnavailableError" }),
    ),
    availability.NOT_AVAILABLE_YET,
  );
});

// ---------------------------------------------------------------------------
// The presentational panels, called directly with absent props
// ---------------------------------------------------------------------------

test("the presentational panels accept absent props", () => {
  const cases: [string, Renderable, Props[]][] = [
    [
      "MaterialityMatrix",
      component("MaterialityMatrix.tsx"),
      [
        { matrix: undefined, risks: undefined },
        { matrix: null, risks: null },
        { matrix: {}, risks: [] },
        {
          matrix: (normalize.normalizeRiskRegister({}) as { matrix: unknown }).matrix,
          risks: [],
        },
      ],
    ],
    [
      "Table5Grid",
      component("Table5Grid.tsx"),
      [
        { table5: undefined },
        { table5: null },
        { table5: {} },
        { table5: normalize.normalizeTable5({}) },
      ],
    ],
    [
      "ConsistencyControlPanel",
      component("ConsistencyControlPanel.tsx"),
      [
        { title: "Control", comparisons: undefined, parameters: undefined, canEdit: false },
        { title: "Control", comparisons: null, parameters: null, canEdit: true },
      ],
    ],
    [
      "ParameterProvenance",
      component("ParameterProvenance.tsx"),
      [{ uses: undefined }, { uses: null }, { uses: [] }, { uses: [null] }],
    ],
    [
      "IrrbbSfCard",
      component("IrrbbSfCard.tsx"),
      [
        { register: undefined },
        { register: null },
        { register: {} },
        { register: { items: null } },
        { register: normalize.normalizePillar2Register({}) },
      ],
    ],
  ];
  for (const [name, target, propSets] of cases) {
    for (const propSet of propSets) {
      assert.doesNotThrow(
        () => render(target, propSet),
        `${name} threw on ${JSON.stringify(propSet)}`,
      );
    }
  }
});

// ---------------------------------------------------------------------------
// The empty states are states, not blanks
// ---------------------------------------------------------------------------

test("an empty risk register states that the thresholds are unavailable", () => {
  const [, riskRegister, hook, normalizer] = PANELS[0];
  stubRead(hook, settled(normalizer({})));
  // D-024: it must say the thresholds are missing, not print one nobody set.
  assert.match(render(riskRegister, PROPS), /thresholds are not available/i);
});

// The three tests below were REWRITTEN (GAP-4 item 3). They used to assert that
// the card never says anything about the mandate, on the reasoning that "the
// commencement date is a governed row this payload does not carry". That was
// true of the payload, not of the product: it left a bank unable to learn from
// the Pillar 2 card whether the framework was already required, which is the
// question the card exists next to. The register now carries the SERVER's
// sentence, resolved through the one non-recording mandate seam. The invariant
// that actually mattered is kept and made sharper: the card never asserts a
// mandate the payload did not state, and never composes one from a date.

/** The server's two verdicts. Neither may appear unless the server sent it. */
const MANDATE_VERDICTS =
  /(applies to reporting dates from|becomes mandatory for reporting dates from)/i;

function mandateFinding(params: Record<string, string>) {
  return {
    code: "pillar2_method_mandate",
    ref: "irrbb",
    params: { method: "irrbb_standardised_framework", ...params },
  };
}

test("with no interest-rate item, the IRRBB card says neither method is in use", () => {
  const text = render(component("IrrbbSfCard.tsx"), {
    register: normalize.normalizePillar2Register({}),
  });
  assert.match(text, /no interest-rate item/i);
  // Absence is never printed as a verdict, in either direction.
  assert.ok(!MANDATE_VERDICTS.test(text), "the card invented a mandate verdict");
  assert.match(text, /does not state whether the standardised framework applies/i);
});

test("the card prints the mandate the SERVER stated, word for word", () => {
  const statement =
    "The Standardised Framework applies to reporting dates from 2026-12-31. " +
    "This date is pending confirmation with the supervisor.";
  const text = render(component("IrrbbSfCard.tsx"), {
    register: normalize.normalizePillar2Register({
      findings: [
        mandateFinding({
          statement,
          mandatory: "true",
          mandatoryFrom: "2026-12-31",
        }),
      ],
    }),
  });
  assert.ok(text.includes(statement), "the server's sentence was not printed");
});

test("a mandate finding with no sentence never becomes one", () => {
  // A payload that carries the DATE but no statement must not be turned into a
  // verdict here: comparing it with the cycle's as-of date is the server's job,
  // and a second opinion on the dashboard is exactly what D-024 forbids.
  const text = render(component("IrrbbSfCard.tsx"), {
    register: normalize.normalizePillar2Register({
      findings: [
        mandateFinding({ statement: "", mandatoryFrom: "2026-12-31" }),
      ],
    }),
  });
  assert.ok(!MANDATE_VERDICTS.test(text));
  assert.ok(!text.includes("2026-12-31"), "a raw governed date leaked");
  assert.match(text, /does not state whether the standardised framework applies/i);
});

test("with the interim method in use, the card points at readiness, not a verdict", () => {
  const text = render(component("IrrbbSfCard.tsx"), {
    register: normalize.normalizePillar2Register({
      items: [
        {
          id: "i1",
          componentKey: "irrbb",
          method: "irrbb_interim_delta_eve",
          methodLabel: "Interim IRRBB add-on from economic-value losses",
        },
      ],
    }),
  });
  assert.match(text, /readiness/i);
  assert.ok(!text.includes("irrbb_interim_delta_eve"), "a raw method key leaked");
});

test("a refused standardised framework item carries the server's sentence, not a zero", () => {
  const refusal =
    "the standardised framework refused this measurement because the banking " +
    "book holds interest-rate options";
  const text = render(component("IrrbbSfCard.tsx"), {
    register: normalize.normalizePillar2Register({
      items: [
        {
          id: "i1",
          componentKey: "irrbb",
          method: "irrbb_standardised_framework",
          methodLabel: "IRRBB standardised framework",
          methodStatus: "not_computable",
          statusDetail: refusal,
          baselineAmount: null,
        },
      ],
    }),
  });
  assert.ok(text.includes(refusal), "the refusal sentence was dropped");
  assert.ok(!/\b0(\.0+)?\b/.test(text), "a refused amount rendered as zero");
});

test("an empty appetite and an empty challenge log show their empty states", () => {
  stubRead("useIcaapAppetite", settled(normalize.normalizeAppetite({})));
  assert.match(render(PANELS[1][1], PROPS), /No appetite metrics yet/);

  stubRead("useIcaapChallenges", settled(normalize.normalizeChallenges({})));
  assert.match(render(PANELS[5][1], PROPS), /No challenges recorded/);
});


// ---------------------------------------------------------------------------
// The surfaces added after the first P2 pass, in the states that matter
// ---------------------------------------------------------------------------

test("an unavailable allocation states the reason instead of an empty grid", () => {
  stubRead(
    "useIcaapCapitalAllocation",
    settled(
      normalize.normalizeAllocation({
        available: false,
        unavailableReason:
          "Compute the capital requirement reconciliation before allocating it.",
      }),
    ),
  );
  const text = render(component("CapitalAllocation.tsx"), PROPS);
  assert.match(text, /Compute the capital requirement reconciliation/);
});

test("triggers that could not be evaluated say so, and never read as clear", () => {
  stubRead(
    "useIcaapCapitalTriggers",
    settled(
      normalize.normalizeCapitalTriggers({
        unavailable: {
          error_code: "capital_plan_not_linked",
          message: "Link this ICAAP's capital plan.",
        },
      }),
    ),
  );
  const text = render(component("CapitalTriggers.tsx"), PROPS);
  assert.match(text, /Link this ICAAP's capital plan/);
  assert.ok(!/Above the trigger levels/.test(text));
});

test("a governed value the control plane does not hold is named on screen", () => {
  stubRead(
    "useIcaapParameterRegister",
    settled(
      normalize.normalizeParameterRegister({ parameters: [], missing: ["car_min"] }),
    ),
  );
  const text = render(component("ParameterRegister.tsx"), PROPS);
  assert.match(text, /car_min/);
});

test("an add-on awaiting confirmation is not shown as being in force", () => {
  stubRead(
    "useIcaapSupervisoryAddons",
    settled(
      normalize.normalizeSupervisoryAddons({
        addons: [{ id: "a", letterReference: "SUP/2026/17" }],
      }),
    ),
  );
  const text = render(component("SupervisoryAddons.tsx"), PROPS);
  assert.match(text, /SUP\/2026\/17/);
  assert.ok(!/In force/.test(text));
  // The standing statement that it is never published is on the panel itself.
  assert.match(text, /never published/);
});

test("the stress tab says what is missing rather than drawing a partial table", () => {
  stubRead("useIcaapStressEvidence", settled(normalize.normalizeStressEvidence({})));
  const text = render(component("StressCapitalPlan.tsx"), PROPS);
  assert.ok(!text.includes("REUSED_PANEL"), "it drew tables it has no figures for");
  assert.match(text, /not linked/i);
});

test("the stress tab reuses the platform's panels once the run is bound", () => {
  const appendix = {
    table1_summary: {
      current: {},
      pre_adverse: [],
      post_adverse: [],
      impact_of_adverse: [],
    },
    table2_capital: [],
    table3_profit_and_loss: [],
    table4_financial_position: [],
    table5_rwa: { rows: [] },
    table6_risk_drivers: { rows: [] },
  };
  stubRead(
    "useIcaapStressEvidence",
    settled(
      normalize.normalizeStressEvidence({
        blocks: [
          {
            id: "1",
            blockType: "appendix_ii",
            currentBinding: {
              sourceRef: { run_id: "run-1" },
              payload: { raw: { raw_appendix_ii: appendix } },
            },
          },
        ],
      }),
    ),
  );
  const text = render(component("StressCapitalPlan.tsx"), PROPS);
  assert.match(text, /REUSED_PANEL/);
});

if (failures > 0) {
  console.error(`${failures} ICAAP P2 resilience test(s) failed`);
  process.exit(1);
}
console.log(
  `ICAAP P2 panels: empty, nulled and 404 payloads all render safely (${PANELS.length} panels + 5 presentational)`,
);
