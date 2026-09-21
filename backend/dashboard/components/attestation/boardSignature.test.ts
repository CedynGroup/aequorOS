/**
 * The Board signature, from the two screens a bank actually reaches it through.
 *
 * Everything below the UI was already built and proved: a real preparer →
 * approver → Board PDF validates, `ordered_slots` is a column, and four layers
 * refuse a Board signature given out of turn. What was missing was any way to
 * TURN IT ON, and any way to be told that this installation has it switched
 * off. Both of those are copy on two shared surfaces, so both are asserted
 * here against the real components rather than described in a report.
 *
 * Three properties, one per group:
 *
 *  1. Settings says which deployment switch is suspending signing, and refuses
 *     the Board control — with the reason — while ICAAP signing is off. A
 *     control that silently did nothing would be worse than one that explains.
 *  2. The Board control exists at all, and only for the one report that can
 *     carry a third signature block.
 *  3. The embedded attestation panel offers the approve-and-sign act on the
 *     authority the SERVER asks for: capital authority for an ICAAP report,
 *     the reporting ladder for every other return. A board member whose whole
 *     authority is over capital was previously never shown the control.
 *
 * Harness: the same transpile-and-evaluate loader as
 * `components/icaap/p3/resilience.test.ts` — read its header for why the
 * components are evaluated here rather than imported — with one difference.
 * Stubs are consulted BEFORE relative resolution, because `AttestationPanel`
 * imports the signing workspace, which imports pdf.js.
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
  ? resolve(__dirname, "../../../components/attestation")
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

// `AttestationPanel` consumes its deep link from `window` on mount. There is no
// DOM here, and a bare object is the whole of what it reads.
const globals = globalThis as unknown as Record<string, unknown>;
globals.window = {
  location: { search: "", pathname: "/submissions/returns" },
  history: { replaceState: () => {} },
  addEventListener: () => {},
  removeEventListener: () => {},
  sessionStorage: { getItem: () => null, removeItem: () => {}, setItem: () => {} },
};

// ---------------------------------------------------------------------------
// The switches under test, as the two screens receive them
// ---------------------------------------------------------------------------

/** What `GET .../signing-policies` reports about this installation. */
let policyList: Record<string, unknown> = {
  policies: [],
  signingSuspendedDeploymentWide: false,
  icaapSigningSuspended: false,
};
/** The projected capabilities for the active institution. */
let moduleScope: Record<string, unknown> = { capitalApprove: false };
/** The scalar session roles. Kept as a fixture precisely so the tests can
 * prove they no longer decide anything on a return. */
let sessionRoles: string[] = [];
/** The `/auth/me` projection for the active institution. */
let effectiveAuthority: Record<string, unknown> | undefined;

/**
 * A Regulatory Reporting projection carrying exactly these permissions.
 *
 * `approve` is projected with `requires_contextual_authorization`, because the
 * server cannot answer "may this officer approve THIS return" without the
 * return in hand. It is read structurally to decide whether to OFFER the act;
 * the server still re-decides, and still refuses the officer who prepared it.
 */
function reportingAuthority(permissions: string[]): Record<string, unknown> {
  return {
    organizationCapabilities: [],
    institutionCapabilities: [
      {
        institutionId: "BK-SAMP0001",
        capabilities: permissions.map((permission) => ({
          module: "reg",
          sensitivity: "restricted",
          permission,
          requiresContextualAuthorization:
            permission === "approve" || permission === "submit",
        })),
      },
    ],
  };
}
/** What `GET .../attestation` reports for the package being rendered. */
let attestationStatus: Record<string, unknown> = {};

const idleMutation = () => ({
  mutate: () => {},
  mutateAsync: async () => undefined,
  isPending: false,
  isError: false,
  error: null,
  reset: () => {},
});

function settled(data: unknown) {
  return {
    data,
    isLoading: false,
    isPending: false,
    error: null,
    refetch: () => {},
  };
}

const passthrough = ({ children }: { children?: unknown }) =>
  el("div", null, children as never);

const hooksModule = new Proxy({} as Record<string, unknown>, {
  get: (_t, property: string) => {
    if (property === "__esModule") return true;
    if (property === "useSigningPolicies") return () => settled(policyList);
    if (property === "useReturnTemplates")
      return () =>
        settled({
          templates: [
            {
              code: "ICAAP-REPORT",
              family: "icaap",
              title: "Internal capital adequacy assessment",
            },
            { code: "BSD3", family: "liquidity", title: "Liquidity position" },
          ],
        });
    if (property === "usePackageAttestation")
      return () => settled(attestationStatus);
    if (property === "useVerifyPackageAttestation")
      return () => settled(undefined);
    return idleMutation;
  },
});

function stubFor(specifier: string): unknown | undefined {
  switch (specifier) {
    // The ceremony itself and its two dialogs: this suite is about which
    // affordance is OFFERED, not about re-testing the workspace — and the real
    // workspace pulls in pdf.js, which has no business in a Node test.
    case "./signing/SigningWorkspace":
      return {
        __esModule: true,
        default: () => el("div", null, "SIGNING_WORKSPACE"),
        isPlaceableRole: (role: string) =>
          role === "preparer" || role === "approver",
      };
    case "./CertifyDialog":
    case "./VerificationPanel":
      return { __esModule: true, default: () => el("div", null, "CEREMONY") };
    case "next-auth/react":
      return { useSession: () => ({ data: { user: { roles: sessionRoles } } }) };
    case "@/components/shell/BankContext":
      return {
        useModuleScope: () => moduleScope,
        useBankContext: () => ({ bank: { id: "BK-SAMP0001" } }),
      };
    case "@/components/profile/ProfileProvider":
      return {
        useUserProfile: () => ({ effectiveAuthority, isLoading: false }),
      };
    case "@/lib/api/hooks":
      return hooksModule;
    case "@/lib/api/grantAdministration":
      return { useGrantAdministrationAccess: () => true };
    case "@/lib/api/client":
      return {
        isApiError: (value: unknown) =>
          Boolean(value) && (value as { name?: string }).name === "ApiError",
      };
    case "@/lib/api/values":
      return {
        fmtDateUTC: () => "31 Mar 2026",
        fmtTimestamp: () => "31 Mar 2026 10:00",
        isoDate: () => "2026-03-31",
      };
    case "@/components/submissions/shared":
      return { FAMILY_LABELS: { icaap: "ICAAP", liquidity: "Liquidity" } };
    case "@/components/ui/SectionCard":
      return {
        __esModule: true,
        default: ({ title, subtitle, actions, children }: Props) =>
          el(
            "section",
            null,
            el("h3", null, title as never),
            el("p", null, subtitle as never),
            actions as never,
            children as never,
          ),
      };
    case "@/components/ui/StatusPill":
      return { __esModule: true, default: passthrough };
    case "@/components/ui/CopyButton":
      return { __esModule: true, default: () => el("span") };
    case "@/components/ui/QueryBoundary":
      return { __esModule: true, default: passthrough, ErrorPanel: passthrough };
    case "@/components/ui/Skeleton":
      return { SkeletonCard: () => el("div", null, "LOADING") };
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
const APP_ROOT = resolve(HERE, "../..");

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

function resolveAliased(specifier: string): string | null {
  const base = join(APP_ROOT, specifier.slice("@/".length));
  for (const candidate of [
    `${base}.tsx`,
    `${base}.ts`,
    join(base, "index.ts"),
  ]) {
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
    // Stub FIRST, unlike the P3 harness: the panels under test import the
    // signing workspace by relative path, and loading the real one would drag
    // pdf.js into a Node process for no gain.
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

function component(name: string): Renderable {
  return loadModule(join(HERE, name)).default as Renderable;
}

function textOf(node: unknown): string {
  if (node === null || node === undefined || node === false) return "";
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join(" ");
  return textOf((node as { children?: unknown }).children);
}

function mount(target: Renderable, props: Props = {}) {
  let tree: ReturnType<typeof create> | undefined;
  act(() => {
    tree = create(el(target, props));
  });
  return {
    text: () => textOf(tree!.toJSON()),
    /** Drive a controlled input the way the browser would. */
    change: (id: string, value: string) => {
      const field = tree!.root.findByProps({ id }) as unknown as {
        props: { onChange: (event: unknown) => void };
      };
      act(() => field.props.onChange({ target: { value } }));
    },
    checkbox: (index: number) => {
      const boxes = tree!.root.findAllByProps({ type: "checkbox" }) as unknown as {
        props: { checked: boolean; disabled?: boolean };
      }[];
      return boxes[index];
    },
    unmount: () => act(() => tree!.unmount()),
  };
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function policy(overrides: Props = {}): Record<string, unknown> {
  return {
    source: "platform_default",
    policyId: null,
    requireSignature: true,
    requireSignedPdf: true,
    distinctSigners: true,
    orderedSlots: true,
    requiredAttachments: [],
    requiredSignatures: [
      { role: "preparer", minCount: 1, officerTitles: [] },
      { role: "approver", minCount: 1, officerTitles: [] },
    ],
    ...overrides,
  };
}

function status(overrides: Props = {}): Record<string, unknown> {
  return {
    packageId: "22222222-2222-4222-8222-222222222222",
    attestationState: "preparer_certified",
    attestationCycle: 1,
    certificationDigest: null,
    certifiedAt: null,
    fullyCertifiedAt: null,
    voidedAt: null,
    voidReason: null,
    policy: policy(),
    outstanding: [{ role: "approver", count: 1 }],
    signatures: [],
    recipients: [],
    canSubmit: false,
    ...overrides,
  };
}

const PANEL_PROPS = {
  bankId: "BK-SAMP0001",
  packageId: "22222222-2222-4222-8222-222222222222",
  returnLabel: "ICAAP · 31 Dec 2025 v1",
  packageStatus: "validated",
  validationClean: true,
};

function resetSwitches(): void {
  policyList = {
    policies: [],
    signingSuspendedDeploymentWide: false,
    icaapSigningSuspended: false,
  };
  moduleScope = { capitalApprove: false };
  sessionRoles = [];
  effectiveAuthority = reportingAuthority(["view"]);
  attestationStatus = status();
}

// ---------------------------------------------------------------------------
// 1. Settings says which switch is suspending signing
// ---------------------------------------------------------------------------

test("ICAAP signing switched off is stated in Settings, not left silent", () => {
  resetSwitches();
  policyList = { ...policyList, icaapSigningSuspended: true };
  const panel = mount(component("SigningPolicyPanel.tsx"));
  const text = panel.text();
  assert.ok(
    text.includes("The ICAAP assessment is not signed in this installation"),
    "Settings did not say that this installation has ICAAP signing switched off",
  );
  assert.ok(
    !text.includes("No return currently requires a signature"),
    "an ICAAP-only suspension was reported as a deployment-wide one",
  );
  panel.unmount();
});

test("the deployment-wide switch is stated in the same place", () => {
  resetSwitches();
  policyList = { ...policyList, signingSuspendedDeploymentWide: true };
  const panel = mount(component("SigningPolicyPanel.tsx"));
  assert.ok(
    panel.text().includes("No return currently requires a signature"),
    "Settings listed policies without saying none of them is being applied",
  );
  panel.unmount();
});

test("nothing is claimed when neither switch is suspending anything", () => {
  resetSwitches();
  const text = mount(component("SigningPolicyPanel.tsx")).text();
  assert.ok(!text.includes("not signed in this installation"));
  assert.ok(!text.includes("No return currently requires a signature"));
});

// ---------------------------------------------------------------------------
// 2. The Board control: offered where it can exist, refused with a reason
//    where the installation has suspended it
// ---------------------------------------------------------------------------

test("the Board control is offered for the assessment that can carry it", () => {
  resetSwitches();
  const panel = mount(component("SigningPolicyPanel.tsx"));
  assert.ok(
    !panel.text().includes("Board signature"),
    "a Board control was offered before any scope had been chosen",
  );
  panel.change("policy-return-family", "icaap");
  const text = panel.text();
  assert.ok(
    text.includes("A member of the Board must sign the assessment"),
    "scoping a policy to the assessment offered no Board control",
  );
  assert.ok(
    text.includes("Board resolution is still filed with the report"),
    "the Board control did not say that it never replaces the resolution",
  );
  panel.unmount();
});

test("a return whose page comes from the regulator is offered no Board slot", () => {
  resetSwitches();
  const panel = mount(component("SigningPolicyPanel.tsx"));
  panel.change("policy-return-family", "liquidity");
  assert.ok(
    !panel.text().includes("A member of the Board must sign"),
    "a Board signature was offered on a return with no third block to sign",
  );
  panel.unmount();
});

test("the Board control is refused, with its reason, while the switch is off", () => {
  resetSwitches();
  policyList = { ...policyList, icaapSigningSuspended: true };
  const panel = mount(component("SigningPolicyPanel.tsx"));
  panel.change("policy-return-family", "icaap");
  const text = panel.text();
  assert.ok(
    text.includes("A member of the Board must sign the assessment"),
    "the Board control vanished instead of explaining itself",
  );
  assert.ok(
    text.includes(
      "the ICAAP assessment is not signed in this installation. Ask your AequorOS administrator",
    ),
    "the Board control was dead rather than refused with a reason",
  );
  // Refused in fact, not only in words.
  const boardBox = panel
    .checkbox(0)
    // The Board control is the first checkbox on the form; the flags follow.
    .props;
  assert.equal(
    boardBox.disabled,
    true,
    "the Board control was still operable while the installation had it off",
  );
  panel.unmount();
});

test("ordered signing is offered, and forced once the Board signs", () => {
  resetSwitches();
  const panel = mount(component("SigningPolicyPanel.tsx"));
  panel.change("policy-return-family", "icaap");
  assert.ok(
    panel.text().includes("Signatures must be given in order"),
    "no control for the ordering the whole lock chain depends on",
  );
  assert.ok(
    panel
      .text()
      .includes("Each signature seals the ones given before it"),
    "ordering was offered without saying why it matters",
  );
  panel.unmount();
});

// ---------------------------------------------------------------------------
// 3. Who is OFFERED the approve-and-sign act
// ---------------------------------------------------------------------------

test("an ICAAP checker is offered the act on capital authority alone", () => {
  resetSwitches();
  moduleScope = { capitalApprove: true };
  sessionRoles = [];
  const text = mount(component("AttestationPanel.tsx"), {
    ...PANEL_PROPS,
    returnFamily: "icaap",
  }).text();
  assert.ok(
    text.includes("Approve and certify") || text.includes("Approve and sign"),
    "a board member holding capital approval authority was offered nothing",
  );
});

test("a scalar approver with no capital standing is offered nothing on an ICAAP", () => {
  resetSwitches();
  moduleScope = { capitalApprove: false };
  sessionRoles = ["approver", "admin"];
  const text = mount(component("AttestationPanel.tsx"), {
    ...PANEL_PROPS,
    returnFamily: "icaap",
  }).text();
  assert.ok(
    text.includes("capital approval authority for this institution"),
    "the panel did not name the authority the server actually asks for",
  );
  assert.ok(
    !text.includes("Approving requires the approver role"),
    "an ICAAP report explained itself with the prudential ladder's sentence",
  );
});

test("every other return asks the projection, not the session's role", () => {
  // The rule these two cases pin (docs/filing_workflow_redesign.md §4b.3): the
  // act is offered on the authority the principal actually HOLDS, projected by
  // the server for this institution. A scalar role is not that authority, and a
  // panel computing `roles.includes('approver')` is how a board member holding
  // a correct binding was never offered the control they held.
  resetSwitches();
  moduleScope = { capitalApprove: false };
  sessionRoles = ["approver", "admin"];
  effectiveAuthority = reportingAuthority(["view"]);
  const withoutBinding = mount(component("AttestationPanel.tsx"), {
    ...PANEL_PROPS,
    returnFamily: "liquidity",
  });
  assert.ok(
    !withoutBinding.text().includes("Approve and sign"),
    "a scalar role alone was enough to be offered the checker act",
  );
  assert.ok(
    withoutBinding.text().includes("Awaiting a checker signature"),
    "the panel did not say what it is waiting for",
  );
  withoutBinding.unmount();

  resetSwitches();
  moduleScope = { capitalApprove: false };
  sessionRoles = [];
  effectiveAuthority = reportingAuthority(["view", "approve"]);
  const withBinding = mount(component("AttestationPanel.tsx"), {
    ...PANEL_PROPS,
    returnFamily: "liquidity",
  });
  assert.ok(
    withBinding.text().includes("Approve and certify") ||
      withBinding.text().includes("Approve and sign"),
    "an officer holding Regulatory Reporting approval authority was offered nothing",
  );
  withBinding.unmount();
});

test("the suspended assessment says a policy cannot bring signing back", () => {
  resetSwitches();
  attestationStatus = status({
    attestationState: "unsigned",
    policy: policy({
      requireSignature: false,
      requireSignedPdf: false,
      source: "icaap_signing_disabled",
    }),
    outstanding: [],
  });
  const text = mount(component("AttestationPanel.tsx"), {
    ...PANEL_PROPS,
    returnFamily: "icaap",
  }).text();
  assert.ok(
    text.includes("The assessment is not signed in this installation"),
    "the signer was not told why there is nothing to sign",
  );
  assert.ok(
    text.includes("ask your AequorOS administrator to switch ICAAP signing on"),
    "the signer was not told who can change it",
  );
  assert.ok(
    !text.includes("Configure a policy under Regulatory Reporting"),
    "the signer was sent to a screen that cannot lift a deployment switch",
  );
});

if (failures > 0) {
  console.error(`${failures} Board signature test(s) failed`);
  process.exit(1);
}
console.log("Board signature: Settings controls and offered acts all checked");
