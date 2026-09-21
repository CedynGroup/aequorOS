/**
 * Every ICAAP review and filing panel, rendered for real, against the payloads
 * that would break it.
 *
 * Same harness and the same reasoning as `components/icaap/p2/resilience.test.ts`
 * — read its header for why the components are transpiled and evaluated here
 * rather than imported. Each panel is rendered under:
 *
 *   (a) an EMPTY payload `{}` normalised — the shape an unfinished API sends;
 *   (a-raw) the same body UNNORMALISED — the belt to the normaliser's braces;
 *   (b) NULLED optional fields;
 *   (c) a 404 — the route does not exist on this deployment, which must render
 *       the "not available yet" sentence and NOT a red error;
 *   (d) 403 / 409 / 500 — genuine failures, which must still look like errors.
 *
 * Plus the rules that are specific to these screens and are the whole point of
 * them: a rehearsal says so wherever it can appear, no control is offered on an
 * unreadable payload, and the server's own refusal sentence reaches the reader.
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
  ? resolve(__dirname, "../../../../components/icaap/p3")
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

function settledStub(data: unknown) {
  return {
    data,
    isLoading: false,
    isPending: false,
    error: null,
    refetch: () => {},
  };
}

function stubFor(specifier: string): unknown | undefined {
  switch (specifier) {
    case "@/lib/api/icaapFiling":
      return hooksModule;
    case "@/lib/api/hooks":
      return new Proxy({} as Record<string, unknown>, {
        get: (_t, property: string) =>
          property === "__esModule"
            ? true
            : property === "useRegulatoryPackage"
              ? () => settledStub(undefined)
              : idleMutation,
      });
    case "@/components/shell/BankContext":
      // Full authority, so every control renders and every branch is exercised.
      return {
        useModuleScope: () => ({
          capitalEdit: true,
          capitalApprove: true,
          capitalExport: true,
        }),
        useBankContext: () => ({ bank: { id: "BK-SAMP0001" } }),
      };
    case "@/lib/format":
      return {
        fmtLocale: () => "en-GB",
        regShort: () => "the regulator",
        centralBankName: () => "the central bank",
        currencyCode: () => "XXX",
      };
    case "@/lib/api/client":
      return {
        apiBaseUrl: "http://127.0.0.1/api/v1",
        isApiError: (value: unknown) =>
          Boolean(value) && (value as { name?: string }).name === "ApiError",
        isModuleUnavailable: (value: unknown) =>
          Boolean(value) &&
          (value as { name?: string }).name === "ModuleUnavailableError",
      };
    case "@/lib/api/token":
      return { getAccessToken: () => null };
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
        FieldLabel: ({ label, children, hint }: Props) =>
          el("label", null, label as never, children as never, hint as never),
        INPUT_CLASS: "",
      };
    // The platform's own filing surfaces, reused unchanged. Stubbed because
    // this suite is about the ICAAP panels' resilience, not about re-testing
    // the attestation workspace; what matters is that the ICAAP panel decides
    // correctly whether to mount them at all.
    case "@/components/submissions/FilingChain":
      // Named export as well as the default: `FilingWorkspace` mounts the chain
      // STRIP, and a stub that only answers `default` hands it `undefined`,
      // which throws only on the fixtures that reach that branch.
      return {
        __esModule: true,
        default: () => el("div", null, "REUSED_PANEL"),
        PackageChainStrip: () => el("div", null, "REUSED_PANEL"),
        FilingChainStrip: () => el("div", null, "REUSED_PANEL"),
      };
    case "@/components/submissions/ChecksPanel":
    case "@/components/attestation/AttestationPanel":
    // P2's independent-review and challenge panels, mounted unchanged on the
    // review tab. They have their own resilience suite next door; what this
    // one checks is that the review tab mounts them.
    case "@/components/icaap/p2/AuditReview":
    case "@/components/icaap/p2/ChallengeLog":
      return { __esModule: true, default: () => el("div", null, "REUSED_PANEL") };
    case "@/components/submissions/shared":
      return {
        fmtBytes: () => "1.0 KB",
        PackageStatusPill: passthrough,
        downloadArtifact: async () => {},
        downloadArtifactVersion: async () => {},
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

function component(name: string): Renderable {
  return loadModule(join(HERE, name)).default as Renderable;
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
  "useIcaapStages",
  "useIcaapFreezePreflight",
  "useIcaapFiling",
  "useIcaapDisclosure",
  "useIcaapWorkflowTemplates",
  "usePackageAttachments",
  "useIcaapPackageArtifacts",
  "useIcaapPackageArtifactVersions",
];

function stubRead(hook: string, result: unknown): void {
  for (const key of Object.keys(hookStubs)) delete hookStubs[key];
  for (const name of READS) {
    hookStubs[name] = name === hook ? () => result : () => settled(undefined);
  }
}

function stubAll(result: unknown): void {
  for (const key of Object.keys(hookStubs)) delete hookStubs[key];
  for (const name of READS) hookStubs[name] = () => result;
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

const normalize = loadModule(
  resolve(HERE, "../../../lib/api/icaapFilingNormalize.ts"),
) as Record<string, (value: unknown) => unknown>;
const availability = loadModule(
  resolve(HERE, "../p2/availability.tsx"),
) as { NOT_AVAILABLE_YET: string };
const labels = loadModule(join(HERE, "labels.ts")) as Record<string, string>;

/** The chain a bank with no template of its own starts a composition from. */
function emptyStages(): unknown[] {
  const payload = normalize.normalizeWorkflowTemplates({}) as {
    effectiveStages: unknown[];
  };
  return payload.effectiveStages;
}

const PROPS = {
  bankId: "BK-SAMP0001",
  cycleId: "11111111-1111-4111-8111-111111111111",
  cycleKind: "annual",
  cycleTitle: "ICAAP 2026",
  packageId: "22222222-2222-4222-8222-222222222222",
  canEdit: true,
  canApprove: true,
  canExport: true,
  isRehearsal: false,
};

/** Hook-driven panels: [name, component, the read it depends on, normaliser]. */
const PANELS: [string, Renderable, string, (v: unknown) => unknown, Props?][] = [
  [
    "ReviewWorkspace",
    component("ReviewWorkspace.tsx"),
    "useIcaapStages",
    normalize.normalizeStages,
  ],
  [
    "FreezeCard",
    component("FreezeCard.tsx"),
    "useIcaapFreezePreflight",
    normalize.normalizePreflight,
    { stages: normalize.normalizeStages({}) },
  ],
  [
    "FilingWorkspace",
    component("FilingWorkspace.tsx"),
    "useIcaapFiling",
    normalize.normalizeFiling,
  ],
  [
    "PackageAttachmentsCard",
    component("PackageAttachmentsCard.tsx"),
    "usePackageAttachments",
    normalize.normalizePackageAttachments,
  ],
  [
    "FilingDownloads",
    component("FilingDownloads.tsx"),
    "useIcaapPackageArtifacts",
    normalize.normalizeArtifacts,
  ],
  [
    "DisclosurePanel",
    component("DisclosurePanel.tsx"),
    "useIcaapDisclosure",
    normalize.normalizeDisclosure,
  ],
  [
    "WorkflowTemplates",
    component("WorkflowTemplates.tsx"),
    "useIcaapWorkflowTemplates",
    normalize.normalizeWorkflowTemplates,
  ],
];

/** Presentational panels, given their data as a prop. */
const PRESENTATIONAL: [string, Renderable, Props][] = [
  [
    "ReviewTimeline",
    component("ReviewTimeline.tsx"),
    { stages: normalize.normalizeStages({}) },
  ],
  [
    "SigningCard",
    component("SigningCard.tsx"),
    {
      ...PROPS,
      filing: normalize.normalizeFiling({}),
      returnLabel: "ICAAP-REPORT",
      packageStatus: "generated",
      validationClean: false,
    },
  ],
  [
    "SubmitIcaapCard",
    component("SubmitIcaapCard.tsx"),
    { ...PROPS, filing: normalize.normalizeFiling({}), canSubmit: true },
  ],
  [
    "BlockerList",
    component("BlockerList.tsx"),
    { items: [], emptyTitle: "Clear", emptyDescription: "Nothing outstanding." },
  ],
  ["RehearsalNotice", component("RehearsalNotice.tsx"), {}],
  [
    "ChainBuilder",
    component("ChainBuilder.tsx"),
    {
      title: "Compose a review chain",
      seed: emptyStages(),
      saving: false,
      error: null,
      onSave: () => {},
      onClose: () => {},
    },
  ],
];

// ---------------------------------------------------------------------------
// (a) empty  (a-raw) unnormalised  (b) nulled
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
    stages: null,
    viewer: null,
    decisions: null,
    items: null,
    slots: null,
    attachments: null,
    requirements: null,
    blockers: null,
    sections: null,
    withheld: null,
    templates: null,
    effectiveStages: null,
    _package: null,
  };
  for (const [name, target, hook, normalizer, extra] of PANELS) {
    stubRead(hook, settled(normalizer(nulled)));
    assert.doesNotThrow(
      () => render(target, { ...PROPS, ...extra }),
      `${name} threw on nulled fields`,
    );
  }
});

test("every presentational panel renders an empty, normalised prop", () => {
  stubAll(settled(undefined));
  for (const [name, target, props] of PRESENTATIONAL) {
    assert.doesNotThrow(() => render(target, props), `${name} threw`);
  }
});

// ---------------------------------------------------------------------------
// (c) 404 is "not available yet"  (d) 403/409/500 still look like errors
// ---------------------------------------------------------------------------

test("(c) a 404 renders the not-available sentence, not an error", () => {
  for (const [name, target, hook, , extra] of PANELS) {
    stubRead(hook, failedWith(404));
    const text = render(target, { ...PROPS, ...extra });
    assert.ok(
      text.includes(availability.NOT_AVAILABLE_YET),
      `${name} did not render the not-available sentence on 404`,
    );
    assert.ok(
      !text.includes("QUERY_BOUNDARY_ERROR"),
      `${name} rendered an ERROR panel on 404`,
    );
  }
});

test("(d) 403, 409 and 500 still look like errors", () => {
  for (const status of [403, 409, 500]) {
    for (const [name, target, hook, , extra] of PANELS) {
      stubRead(hook, failedWith(status));
      const text = render(target, { ...PROPS, ...extra });
      assert.ok(
        !text.includes(availability.NOT_AVAILABLE_YET),
        `${name} treated ${status} as "not available yet"`,
      );
    }
  }
});

// ---------------------------------------------------------------------------
// The rules these screens exist to enforce
// ---------------------------------------------------------------------------

test("a rehearsal says so on the review tab and on the filing tab", () => {
  for (const [name, target] of [
    ["ReviewWorkspace", component("ReviewWorkspace.tsx")],
    ["FilingWorkspace", component("FilingWorkspace.tsx")],
  ] as const) {
    stubAll(settled(normalize.normalizeStages({})));
    const text = render(target, { ...PROPS, cycleKind: "rehearsal" });
    assert.ok(
      text.includes(labels.REHEARSAL_HEADLINE),
      `${name} did not say that the cycle is a practice run`,
    );
    assert.ok(
      text.includes("never be filed"),
      `${name} did not say that a practice run is never filed`,
    );
  }
});

test("an annual cycle carries no rehearsal notice", () => {
  stubAll(settled(normalize.normalizeStages({})));
  const text = render(component("ReviewWorkspace.tsx"), {
    ...PROPS,
    cycleKind: "annual",
  });
  assert.ok(!text.includes(labels.REHEARSAL_HEADLINE));
});

test("a cycle with no sealed report says there is nothing to file", () => {
  stubRead("useIcaapFiling", settled(normalize.normalizeFiling({})));
  const text = render(component("FilingWorkspace.tsx"), PROPS);
  assert.ok(
    text.includes("Nothing to file yet"),
    "the filing tab did not say that there is nothing to file",
  );
});

test("the server's own refusal sentence reaches the reader", () => {
  const sentence = "The board resolution has not been attached.";
  stubAll(settled(undefined));
  const text = render(component("SubmitIcaapCard.tsx"), {
    ...PROPS,
    canSubmit: true,
    filing: normalize.normalizeFiling({
      _package: { id: "p", status: "approved" },
      blockers: [
        {
          code: "attachments_missing",
          severity: "blocking",
          scope: "attachment",
          message: sentence,
        },
      ],
    }),
  });
  assert.ok(text.includes(sentence), "the server's refusal was not shown");
});

test("a signature slot waiting its turn says why, and names no raw role token", () => {
  stubAll(settled(undefined));
  const text = render(component("SigningCard.tsx"), {
    ...PROPS,
    returnLabel: "ICAAP-REPORT",
    packageStatus: "generated",
    validationClean: false,
    filing: normalize.normalizeFiling({
      _package: { id: "p" },
      slots: [
        { role: "preparer", required: true, signedBy: "A. Preparer" },
        { role: "board", required: true, blockedBy: "approver" },
      ],
    }),
  });
  assert.ok(text.includes("Waiting its turn"));
  assert.ok(text.includes("signs first"));
  // No raw enum on screen. The English word "approver" is fine; an underscored
  // token from the wire is not.
  assert.ok(
    !/\b[a-z]+_[a-z_]+\b/.test(text),
    "a raw token reached the screen",
  );
});

test("the exposure-draft reality is stated on the freeze card, not hidden", () => {
  // D-006: most Ghana sections are still built from an exposure draft, so a
  // real filing cannot be frozen. The screen says so in its own words above
  // the blocker list, rather than leaving a reader to decode a refusal code.
  stubRead(
    "useIcaapFreezePreflight",
    settled(
      normalize.normalizePreflight({
        ready: false,
        items: [
          {
            code: "framework_pending_primary_text",
            severity: "blocking",
            scope: "cycle",
            message: "Sections d-q are awaiting the regulator's published text.",
          },
        ],
      }),
    ),
  );
  const text = render(component("FreezeCard.tsx"), {
    ...PROPS,
    stages: normalize.normalizeStages({}),
  });
  assert.ok(
    text.includes(labels.AWAITING_REGULATOR_TEXT_TITLE),
    "the freeze card did not state that the framework is an exposure draft",
  );
  // The server's own sentence still reaches the reader underneath it.
  assert.ok(text.includes("awaiting the regulator"));
  // And the raw code never does.
  assert.ok(!text.includes("framework_pending_primary_text"));
});

test("an unreadable stages payload offers no decision, submission or freeze control", () => {
  stubAll(settled(normalize.normalizeStages({})));
  const text = render(component("ReviewWorkspace.tsx"), PROPS);
  for (const control of [
    "Put forward for review",
    "Record my review",
    "Seal the report",
  ]) {
    assert.ok(
      !text.includes(control),
      `"${control}" was offered on a payload that grants nothing`,
    );
  }
});

// ---------------------------------------------------------------------------
// The review-chain composer (GAP-4 item 1)
// ---------------------------------------------------------------------------
//
// The screen used to show the chain and nothing else, deliberately: a chain a
// client would accept and the server would refuse is worse than no composer.
// The composer exists now on the only terms that reason allows — it mirrors no
// rule, and the refusal it prints is the server's.

const builder = loadModule(join(HERE, "ChainBuilder.tsx")) as {
  referenceFor: (title: string) => string;
  toDraft: (stages: unknown) => unknown[];
  toPayload: (stages: unknown) => Record<string, unknown>[];
  saveBlocked: (input: {
    stageCount: number;
    reason: string;
    saving: boolean;
  }) => boolean;
};

const REASON = "The Board Risk Committee now sits between the CRO and the CEO.";

test("the composer holds Save back for the REQUEST, never for the chain", () => {
  // A chain the server refuses outright — one review stage, no preparation, no
  // signature, nothing sealing. The composer still offers to send it, because
  // the server is the only thing entitled to say no.
  assert.equal(
    builder.saveBlocked({ stageCount: 1, reason: REASON, saving: false }),
    false,
    "the composer pre-judged a chain the server owns",
  );
  // The two things it DOES hold back are payload facts, not chain rules.
  assert.equal(
    builder.saveBlocked({ stageCount: 0, reason: REASON, saving: false }),
    true,
  );
  assert.equal(
    builder.saveBlocked({ stageCount: 2, reason: "  short ", saving: false }),
    true,
  );
  assert.equal(
    builder.saveBlocked({ stageCount: 2, reason: REASON, saving: true }),
    true,
  );
});

test("the composer's source mirrors none of the server's stage rules", () => {
  const source = readFileSync(join(HERE, "ChainBuilder.tsx"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^\s*\/\/.*$/gm, " ");
  for (const code of [
    "stage_prepare_required",
    "stage_attest_required",
    "stage_review_required",
    "stage_freeze_required",
    "stage_freeze_not_approval",
    "stage_freeze_not_before_attest",
    "stage_seq_not_contiguous",
    "stage_keys_not_unique",
  ]) {
    assert.ok(
      !source.includes(code),
      `ChainBuilder names ${code} — the rule belongs to validate_stages alone`,
    );
  }
});

test("reordering IS the numbering, and titles become the wire references", () => {
  const stages = [
    {
      stageKey: "prepare",
      title: " Prepared by the ALM team ",
      kind: "prepare",
      officerTitles: "Head of ALM,  Treasurer ,",
      freezeOnApprove: false,
    },
    {
      stageKey: "board",
      title: "Board signature",
      kind: "attest",
      officerTitles: "",
      freezeOnApprove: false,
    },
  ];
  const payload = builder.toPayload(stages);
  assert.deepEqual(
    payload.map((row) => row.seq),
    [1, 2],
  );
  assert.equal(payload[0].title, "Prepared by the ALM team");
  assert.deepEqual(payload[0].officerTitles, ["Head of ALM", "Treasurer"]);
  assert.deepEqual(payload[1].officerTitles, []);
  assert.equal(payload[1].decisionKind, "attest");

  assert.equal(builder.referenceFor("Board Risk Committee"), "board_risk_committee");
  assert.equal(builder.referenceFor("  CEO / CFO  "), "ceo_cfo");
});

test("a refusal is printed in the SERVER's words, not paraphrased", () => {
  const refusal =
    "The freeze stage must be the one immediately before the Board " +
    "attestation: the Board signs the document the freeze produced.";
  stubAll(settled(undefined));
  const text = render(component("ChainBuilder.tsx"), {
    title: "Compose a review chain",
    seed: emptyStages(),
    saving: false,
    error: refusal,
    onSave: () => {},
    onClose: () => {},
  });
  assert.ok(text.includes(refusal), "the server's refusal was not shown");
});

test("the chain screen offers composing, and offers editing an open draft", () => {
  const payload = normalize.normalizeWorkflowTemplates({
    effectiveSource: "framework_default",
    effectiveStages: [
      {
        seq: 1,
        stageKey: "prepare",
        title: "Preparation",
        decisionKind: "prepare",
        officerTitles: [],
        freezeOnApprove: false,
      },
    ],
    templates: [
      {
        id: "t1",
        version: 1,
        status: "draft",
        stages: [],
        reason: REASON,
        proposedBy: "u1",
        createdAt: "2026-01-01T00:00:00Z",
      },
    ],
  });
  stubRead("useIcaapWorkflowTemplates", settled(payload));
  const text = render(component("WorkflowTemplates.tsx"), PROPS);
  assert.ok(
    text.includes(labels.BUILDER_EDIT_DRAFT),
    "an open draft offered no way to edit its stages",
  );
  // One proposal at a time: the server answers `workflow_template_open`, so the
  // screen says why rather than offering a compose the server would refuse.
  assert.ok(text.includes(labels.BUILDER_START_FROM_EFFECTIVE));
});

if (failures > 0) {
  console.error(`${failures} ICAAP P3 resilience test(s) failed`);
  process.exit(1);
}
console.log(
  `${PANELS.length} ICAAP filing panels + ${PRESENTATIONAL.length} presentational render safely`,
);
