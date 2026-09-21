/**
 * The Returns workspace shows the officer's own work, not the union of
 * everyone's (docs/filing_workflow_redesign.md §4b).
 *
 * Three properties, and the first is the one the whole redesign exists for:
 *
 *  1. **A Preparer session contains no affordance for the regulator's channel,
 *     anywhere on the returns surface.** Not greyed out — absent. Asserted
 *     twice: exhaustively over the pure surface model, and against the rendered
 *     output of every component a non-transmitting officer actually sees.
 *  2. **A control that IS this officer's but is not yet their turn is disabled
 *     WITH its reason.** A disabled control with nothing to say is the defect
 *     the old screen was made of.
 *  3. **The machine check is never a person.** The word "Validated" has left the
 *     product for that meaning; it belongs to the Validator, the officer who
 *     files the return.
 *
 * Harness: the transpile-and-evaluate loader used by
 * `components/attestation/boardSignature.test.ts` — read its header for why the
 * components are evaluated here rather than imported.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import ts from "typescript";
import * as React from "react";
import { act, create } from "react-test-renderer";

import {
  UNRESOLVED_FILING_AUTHORITY,
  filingAuthorityFor,
  type FilingAuthority,
} from "../../lib/submissions/filingAuthority";
import {
  buildFilingChain,
  type FilingChainInput,
} from "../../lib/submissions/filingChain";
import {
  heldStages,
  noActionExplanation,
  primaryFilingAction,
  stageForStatus,
  surfaceSections,
  type SurfaceInput,
} from "../../lib/submissions/returnsSurface";

const HERE = __dirname.includes(".test-out")
  ? resolve(__dirname, "../../../components/submissions")
  : __dirname;
const ROOT = resolve(HERE, "../..");

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

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** Every package status the lifecycle can present. */
const STATUSES = [
  "draft",
  "generated",
  "validated",
  "pending_approval",
  "approved",
  "submitted",
  "acknowledged",
  "rejected",
  "declined",
  "superseded",
] as const;

type Status = (typeof STATUSES)[number];

function authority(overrides: Partial<FilingAuthority>): FilingAuthority {
  return {
    isResolved: true,
    isReadOnly: false,
    mayPrepare: false,
    mayRunChecks: false,
    mayExport: false,
    mayApprove: false,
    mayTransmit: false,
    ...overrides,
  };
}

const PREPARER = authority({
  mayPrepare: true,
  mayRunChecks: true,
  mayExport: true,
});
const APPROVER = authority({ mayApprove: true });
const VALIDATOR = authority({ mayTransmit: true });
const READER = authority({});
const EXAMINER = authority({ isReadOnly: true });

function input(
  who: FilingAuthority,
  status: Status,
  overrides: Partial<SurfaceInput> = {},
): SurfaceInput {
  return {
    authority: who,
    status,
    hasPackage: true,
    checksClean: true,
    checkErrors: 0,
    clearedToSubmit: true,
    outstandingSummary: null,
    signingRequired: true,
    isRehearsal: false,
    pendingReupload: false,
    canPoll: true,
    regulatorName: "BoG",
    ...overrides,
  };
}

/** Vocabulary that belongs to the regulator's channel and to nobody else. */
const CHANNEL_WORDS =
  /orass|sandbox|downtime|\.eml\b|re-upload|resubmi|\bchannel\b|\bportal\b|\bfallback\b/i;

// ---------------------------------------------------------------------------
// 1. The channel is ABSENT for everyone but the officer who holds it
// ---------------------------------------------------------------------------

test("no surface but the Validator's ever contains the regulator's channel", () => {
  for (const status of STATUSES) {
    for (const [name, who] of [
      ["preparer", PREPARER],
      ["approver", APPROVER],
      ["reader", READER],
      ["examiner", EXAMINER],
    ] as const) {
      const sections = surfaceSections(input(who, status));
      for (const forbidden of ["transmission", "events", "resubmission"]) {
        assert.ok(
          !sections.includes(forbidden as never),
          `${name} was offered the ${forbidden} surface at '${status}'`,
        );
      }
    }
  }
});

test("the Validator does get it, or the return could never be filed", () => {
  const approved = surfaceSections(input(VALIDATOR, "approved"));
  assert.ok(approved.includes("transmission"));
  assert.ok(approved.includes("events"));
  const filed = surfaceSections(input(VALIDATOR, "submitted"));
  assert.ok(filed.includes("resubmission"));
});

test("no act that reaches a regulator is ever offered to another role", () => {
  const transmitting = new Set(["transmit", "poll", "reupload"]);
  for (const status of STATUSES) {
    for (const [name, who] of [
      ["preparer", PREPARER],
      ["approver", APPROVER],
      ["reader", READER],
      ["examiner", EXAMINER],
    ] as const) {
      const action = primaryFilingAction(input(who, status));
      assert.ok(
        action === null || !transmitting.has(action.kind),
        `${name} was offered '${action?.kind}' at '${status}'`,
      );
    }
  }
});

// ---------------------------------------------------------------------------
// 2. Hidden until resolved; read-only stays read-only
// ---------------------------------------------------------------------------

test("nothing actionable exists before the projection has answered", () => {
  for (const status of STATUSES) {
    const pending = input(UNRESOLVED_FILING_AUTHORITY, status);
    assert.equal(primaryFilingAction(pending), null, `acted at '${status}'`);
    assert.deepEqual(surfaceSections(pending), []);
  }
  assert.match(
    noActionExplanation(input(UNRESOLVED_FILING_AUTHORITY, "generated")),
    /checking what you are authorised/i,
  );
});

test("an examiner is offered nothing, and is told why", () => {
  for (const status of STATUSES) {
    assert.equal(primaryFilingAction(input(EXAMINER, status)), null);
  }
  assert.match(
    noActionExplanation(input(EXAMINER, "approved")),
    /examiner/i,
  );
  assert.deepEqual(heldStages(EXAMINER), []);
});

test("the projection decides the role — an empty one holds no stage", () => {
  assert.deepEqual(heldStages(READER), []);
  assert.match(
    noActionExplanation(input(READER, "generated")),
    /held by other officers/i,
  );
});

// ---------------------------------------------------------------------------
// 3. Disabled means "yours, but not now" — and always says so
// ---------------------------------------------------------------------------

test("every disabled act carries its reason", () => {
  for (const status of STATUSES) {
    for (const who of [PREPARER, APPROVER, VALIDATOR]) {
      for (const checks of [
        { checksClean: true, checkErrors: 0 },
        { checksClean: false, checkErrors: 2 },
      ]) {
        for (const cleared of [true, false]) {
          const action = primaryFilingAction(
            input(who, status, {
              ...checks,
              clearedToSubmit: cleared,
              outstandingSummary: cleared ? null : "1 approver signature",
            }),
          );
          if (!action || action.enabled) continue;
          assert.ok(
            action.reason && action.reason.trim().length > 0,
            `'${action.kind}' at '${status}' was greyed out with nothing to say`,
          );
          assert.ok(
            !/\b(draft|generated|validated|pending_approval|approved|submitted|acknowledged|superseded)\b/.test(
              action.reason,
            ),
            `'${action.kind}' explained itself with a raw status value: ${action.reason}`,
          );
        }
      }
    }
  }
});

test("a practice run is refused a filing, and says which it is", () => {
  const action = primaryFilingAction(
    input(VALIDATOR, "approved", { isRehearsal: true }),
  );
  assert.ok(action);
  assert.equal(action!.enabled, false);
  assert.match(action!.reason ?? "", /practice run/i);
});

test("an unsigned return is refused a filing, and names what is missing", () => {
  const action = primaryFilingAction(
    input(VALIDATOR, "approved", {
      clearedToSubmit: false,
      outstandingSummary: "1 approver signature",
    }),
  );
  assert.ok(action);
  assert.equal(action!.enabled, false);
  assert.match(action!.reason ?? "", /1 approver signature/);
});

test("failing checks stop the preparer, and the reason counts them", () => {
  const action = primaryFilingAction(
    input(PREPARER, "generated", { checksClean: false, checkErrors: 3 }),
  );
  assert.ok(action);
  assert.equal(action!.enabled, false);
  assert.match(action!.reason ?? "", /3 checks are still failing/i);
});

test("a Validator waiting on an earlier stage is told who holds it", () => {
  const waiting = primaryFilingAction(input(VALIDATOR, "pending_approval"));
  assert.ok(waiting);
  assert.equal(waiting!.enabled, false);
  assert.match(waiting!.reason ?? "", /waiting on the approver/i);
});

test("the preparer gets the rework when the regulator returns a filing", () => {
  const action = primaryFilingAction(input(PREPARER, "rejected"));
  assert.ok(action);
  assert.equal(action!.kind, "regenerate");
  assert.equal(action!.enabled, true);
});

// ---------------------------------------------------------------------------
// 4. The capability projection, read the way the server writes it
// ---------------------------------------------------------------------------

function projection(
  permissions: { permission: string; sensitivity?: string }[],
): Parameters<typeof filingAuthorityFor>[0] {
  return {
    organizationCapabilities: [],
    institutionCapabilities: [
      {
        institutionId: "BK-SAMP0001",
        capabilities: permissions.map((entry) => ({
          module: "reg",
          sensitivity: entry.sensitivity ?? "restricted",
          permission: entry.permission,
          requiresContextualAuthorization:
            entry.permission === "approve" || entry.permission === "submit",
        })),
      },
    ],
  } as unknown as Parameters<typeof filingAuthorityFor>[0];
}

test("transmission is read from Regulatory Reporting · Restricted · submit", () => {
  const validator = filingAuthorityFor(
    projection([{ permission: "view" }, { permission: "submit" }]),
    "BK-SAMP0001",
    { resolved: true },
  );
  assert.equal(validator.mayTransmit, true, "the Validator could not file");
  assert.equal(validator.mayApprove, false, "filing implied approving");
  assert.equal(validator.mayPrepare, false, "filing implied preparing");
});

test("a contextual capability still OFFERS the act — the server decides it", () => {
  // `approve` and `submit` are projected with
  // `requires_contextual_authorization`. A reader that dropped them would hide
  // the control from every holder of a correct binding, which is exactly how the
  // ICAAP approve-and-sign affordance became unreachable.
  const approver = filingAuthorityFor(
    projection([{ permission: "view" }, { permission: "approve" }]),
    "BK-SAMP0001",
    { resolved: true },
  );
  assert.equal(approver.mayApprove, true);
  assert.equal(approver.mayTransmit, false);
});

test("covering one institution never covers its sibling", () => {
  const other = filingAuthorityFor(
    projection([{ permission: "submit" }]),
    "BK-OTHER001",
    { resolved: true },
  );
  assert.equal(other.mayTransmit, false);
});

test("filing authority at the wrong classification is not filing authority", () => {
  const published = filingAuthorityFor(
    projection([{ permission: "submit", sensitivity: "published" }]),
    "BK-SAMP0001",
    { resolved: true },
  );
  assert.equal(published.mayTransmit, false);
});

test("an unresolved projection grants nothing at all", () => {
  const pending = filingAuthorityFor(
    projection([{ permission: "submit" }]),
    "BK-SAMP0001",
    { resolved: false },
  );
  assert.deepEqual(pending, UNRESOLVED_FILING_AUTHORITY);
});

// ---------------------------------------------------------------------------
// 5. The chain: who holds it, what was decided, and what is not recorded
// ---------------------------------------------------------------------------

const REGULATOR = { short: "BoG", full: "Bank of Ghana", portal: "ORASS" };

function chainInput(overrides: Partial<FilingChainInput>): FilingChainInput {
  return {
    status: "pending_approval",
    approvals: [],
    attestation: null,
    events: [],
    checksClean: true,
    regulator: REGULATOR,
    resolveOfficer: () => null,
    ...overrides,
  } as FilingChainInput;
}

test("a send-back names where it went, and carries its comment", () => {
  const chain = buildFilingChain(
    chainInput({
      status: "generated",
      approvals: [
        {
          id: "1",
          action: "requested",
          actorUserId: "u1",
          occurredAt: new Date("2026-03-01T10:00:00Z"),
          reason: null,
        },
        {
          id: "2",
          action: "rejected",
          actorUserId: "u2",
          occurredAt: new Date("2026-03-02T10:00:00Z"),
          reason: "Line 12 double-counts the placement maturing 2 April.",
        },
      ] as never,
    }),
  );
  const returned = chain.entries.at(-1)!;
  assert.equal(returned.outcome, "returned");
  assert.equal(returned.returnedTo, "the preparer");
  assert.match(returned.comment ?? "", /double-counts/);
});

test("what the record does not say is reported as not recorded", () => {
  const chain = buildFilingChain(
    chainInput({
      approvals: [
        {
          id: "1",
          action: "requested",
          actorUserId: "u1",
          occurredAt: new Date("2026-03-01T10:00:00Z"),
          reason: null,
        },
      ] as never,
    }),
  );
  const entry = chain.entries[0];
  assert.equal(entry.actorName, null, "an unresolved officer was named anyway");
  assert.equal(entry.round, null, "a round was invented");
  assert.equal(chain.round, null, "a round was invented for the chain");
  assert.equal(
    chain.stages.every((stage) => stage.holder === null),
    true,
    "a stage holder was invented",
  );
});

test("the roster names the officer when the officer can read it", () => {
  const chain = buildFilingChain(
    chainInput({
      resolveOfficer: () => ({ name: "Ama Mensah", title: "Head of Finance" }),
      approvals: [
        {
          id: "1",
          action: "approved",
          actorUserId: "u1",
          occurredAt: new Date("2026-03-01T10:00:00Z"),
          reason: null,
        },
      ] as never,
    }),
  );
  assert.equal(chain.entries[0].actorName, "Ama Mensah");
  assert.equal(chain.entries[0].actorTitle, "Head of Finance");
});

test("the chain is people and stages — never a status called Validated", () => {
  for (const status of STATUSES) {
    const chain = buildFilingChain(chainInput({ status }));
    const copy = [
      chain.position,
      chain.positionDetail,
      chain.next ?? "",
      ...chain.stages.map((stage) => `${stage.title} ${stage.roleName} ${stage.act}`),
    ].join(" ");
    assert.ok(
      !/\bvalidated\b/i.test(copy),
      `'${status}' described a machine check as a person: ${copy}`,
    );
    assert.ok(
      !/\bvalidate\b/i.test(copy),
      `'${status}' offered validation as an act: ${copy}`,
    );
    for (const raw of STATUSES) {
      // Only the wire's own spellings: `generated` and `approved` are ordinary
      // English and belong in a sentence about a return. A snake_case token, or
      // a value in quotes, is the payload leaking onto the screen.
      assert.ok(
        !copy.includes(raw.replace(/_/g, "")) || !raw.includes("_"),
        `'${status}' leaked the raw status value '${raw}'`,
      );
      assert.ok(!copy.includes(raw.includes("_") ? raw : `'${raw}'`));
    }
  }
});

test("exactly one stage holds the return, at every live status", () => {
  for (const status of STATUSES) {
    const chain = buildFilingChain(chainInput({ status }));
    const current = chain.stages.filter((stage) => stage.state === "current");
    assert.ok(
      current.length <= 1,
      `'${status}' claimed ${current.length} officers hold the return`,
    );
    const at = stageForStatus(status);
    if (at !== "closed") {
      assert.equal(
        current.length,
        1,
        `'${status}' claimed nobody holds a live return`,
      );
    }
  }
});

// ---------------------------------------------------------------------------
// 6. Rendered: what a Preparer's screen can actually say
// ---------------------------------------------------------------------------

const el = React.createElement;
type Props = Record<string, unknown>;
type Renderable = Parameters<typeof React.createElement>[0];

const passthrough = ({ children }: { children?: unknown }) =>
  el("div", null, children as never);

function stubFor(specifier: string): unknown | undefined {
  switch (specifier) {
    case "@/lib/format":
      return {
        regShort: () => "BoG",
        centralBankName: () => "Bank of Ghana",
        submissionPortal: () => "ORASS",
      };
    case "@/lib/api/values":
      return { fmtTimestamp: () => "02 Mar 2026 10:00" };
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
    case "@/components/ui/QueryBoundary":
      return { __esModule: true, default: passthrough, ErrorPanel: passthrough };
    case "lucide-react":
      return new Proxy({} as Record<string, unknown>, {
        get: () => () => el("span"),
      });
    default:
      return undefined;
  }
}

const cache = new Map<string, Record<string, unknown>>();

function resolveLocal(from: string, specifier: string): string | null {
  if (!specifier.startsWith(".")) return null;
  const base = resolve(dirname(from), specifier);
  for (const candidate of [`${base}.tsx`, `${base}.ts`]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function resolveAliased(specifier: string): string | null {
  const base = join(ROOT, specifier.slice("@/".length));
  for (const candidate of [`${base}.tsx`, `${base}.ts`]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function loadModule(file: string): Record<string, unknown> {
  const cached = cache.get(file);
  if (cached) return cached;
  const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
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
    const stub = stubFor(specifier);
    if (stub !== undefined) return stub;
    const local = resolveLocal(file, specifier);
    if (local) return loadModule(local);
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
  cache.set(file, moduleObject.exports);
  return moduleObject.exports;
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
  const text = textOf(tree!.toJSON());
  act(() => tree!.unmount());
  return text;
}

test("the chain a Preparer reads never mentions the regulator's channel", () => {
  const panel = loadModule(join(HERE, "FilingChain.tsx")).default as Renderable;
  for (const status of STATUSES) {
    const text = render(panel, {
      status,
      approvals: [],
      attestation: null,
      events: [],
      checksClean: true,
      resolveOfficer: () => null,
      // The Preparer's chain stops at the Validator: the regulator is a stage
      // they never touch, and drawing it invites them to think it is theirs.
      showRegulatorStage: false,
      defaultOpen: true,
    });
    assert.ok(
      !CHANNEL_WORDS.test(text),
      `the chain at '${status}' offered channel vocabulary: ${text}`,
    );
    assert.ok(
      !/\bvalidated\b/i.test(text),
      `the chain at '${status}' still calls a machine check "Validated"`,
    );
  }
});

test("the command bar a Preparer reads offers no way to reach a regulator", () => {
  const bar = loadModule(join(HERE, "ReturnCommandBar.tsx"));
  const CommandBar = bar.ReturnCommandBar as Renderable;
  const PrimaryActionButton = bar.PrimaryActionButton as Renderable;
  const ArtifactGroup = bar.ArtifactGroup as Renderable;

  for (const status of STATUSES) {
    const action = primaryFilingAction(input(PREPARER, status));
    const text = render(CommandBar, {
      identity: "BSD3",
      meta: "31 Mar 2026 · Version 2",
      pills: null,
      artifacts: el(ArtifactGroup as never, {
        kinds: ["pdf", "xlsx", "csv"],
        available: new Set(["pdf"]),
        busyKind: null,
        canExport: true,
        onTake: () => {},
        unavailableReason: "Producing it is the preparer's act.",
      } as never),
      action: action
        ? el(PrimaryActionButton as never, {
            action,
            pending: false,
            onClick: () => {},
          } as never)
        : el("p", null, noActionExplanation(input(PREPARER, status))),
    });
    assert.ok(
      !CHANNEL_WORDS.test(text),
      `the command bar at '${status}' offered channel vocabulary: ${text}`,
    );
    assert.ok(
      !/\bvalidate\b/i.test(text),
      `the command bar at '${status}' still offers a Validate button: ${text}`,
    );
  }
});

test("a disabled act states its reason on screen, not only on hover", () => {
  const bar = loadModule(join(HERE, "ReturnCommandBar.tsx"));
  const PrimaryActionButton = bar.PrimaryActionButton as Renderable;
  const action = primaryFilingAction(
    input(PREPARER, "generated", { checksClean: false, checkErrors: 2 }),
  );
  const text = render(PrimaryActionButton, {
    action,
    pending: false,
    onClick: () => {},
  });
  assert.match(text, /2 checks are still failing/i);
});

// ---------------------------------------------------------------------------
// 7. Structural: the workspace cannot spell what it must not offer
// ---------------------------------------------------------------------------

function source(relative: string): string {
  return readFileSync(join(ROOT, relative), "utf8");
}

const WORKSPACE = "app/(app)/submissions/returns/page.tsx";

test("the regulator's channel is mounted only behind its own section", () => {
  const page = source(WORKSPACE);
  for (const [component, section] of [
    ["TransmissionCard", "transmission"],
    ["EventsFeed", "events"],
    ["ResubmissionCard", "resubmission"],
  ] as const) {
    const mounted = page.split(`<${component}`).length - 1;
    assert.equal(
      mounted,
      1,
      `<${component}> is mounted ${mounted} times — one guard cannot cover them all`,
    );
    const guard = `{showSection('${section}') && (`;
    assert.ok(
      page.includes(guard),
      `the ${section} surface has no ${guard} guard`,
    );
    const guardAt = page.indexOf(guard);
    const mountAt = page.indexOf(`<${component}`);
    assert.ok(
      guardAt >= 0 && guardAt < mountAt,
      `<${component}> is mounted outside its ${section} guard`,
    );
  }
});

test("the workspace itself cannot even spell the portal's name", () => {
  const page = source(WORKSPACE);
  // Stripped of comments: this is about what the screen can RENDER.
  const code = page
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^\s*\/\/.*$/gm, " ");
  for (const word of ["ORASS", "Sandbox", "sandbox", "downtime", ".eml"]) {
    assert.ok(
      !code.includes(word),
      `the workspace names '${word}' — channel copy belongs in TransmissionCard`,
    );
  }
});

test("there is no Validate button anywhere on the returns surface", () => {
  for (const file of [
    WORKSPACE,
    "components/submissions/ReturnCommandBar.tsx",
    "components/submissions/ChecksPanel.tsx",
    "components/submissions/FilingChain.tsx",
  ]) {
    const code = source(file)
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .replace(/^\s*\/\/.*$/gm, " ");
    assert.ok(
      !/["'>]\s*Validate\b/.test(code),
      `${file} still offers a Validate control`,
    );
    assert.ok(
      !/["'>]\s*Validated\b/.test(code),
      `${file} still labels something "Validated"`,
    );
  }
});

test("the lifecycle pill calls the machine result what it is", () => {
  const shared = source("components/submissions/shared.tsx");
  assert.match(
    shared,
    /validated:\s*'Checks passed'/,
    "the status pill still reads 'Validated' — the word belongs to the Validator",
  );
});

test("the stepper is gone, not merely unused", () => {
  assert.ok(
    !existsSync(join(ROOT, "components/submissions/LifecycleStepper.tsx")),
    "the six-status stepper is still in the tree and can be re-mounted",
  );
});

if (failures > 0) {
  console.error(`${failures} filing surface test(s) failed`);
  process.exit(1);
}
console.log(
  "Filing surface: role scoping, disabled reasons, chain copy and channel absence all checked",
);
