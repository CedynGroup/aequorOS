import assert from "node:assert/strict";
import NodeModule from "node:module";
import path from "node:path";
import React, { type ReactNode } from "react";
import { act, create, type ReactTestInstance } from "react-test-renderer";
import type {
  ForecastAssumptionRegisterRead,
  ForecastAssumptionVersionRead,
  ForecastRunRead,
  ForecastRunSummaryRead,
} from "@aequoros/risk-service-api";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const bankId = "BK-AAAAAAAA";
const drivers = {
  loanGrowthPct: "5",
  depositGrowthPct: "5",
  nimPct: "5",
  costToIncomePct: "50",
  creditLossRatePct: "1",
  fxDepreciationPct: "1",
  dividendPayoutPct: "30",
};
function version(
  number: number,
  status: ForecastAssumptionVersionRead["status"] = "draft",
): ForecastAssumptionVersionRead {
  return {
    id: `version-${number}`,
    bankId,
    versionNumber: number,
    status,
    effectiveFrom: new Date("2026-09-01"),
    presets: { base: drivers, adverse: drivers, severelyAdverse: drivers },
    changeNote: `Proposal ${number}`,
    createdBy: "maker",
    createdByName: "Maker",
    createdAt: new Date("2026-09-01"),
    updatedAt: new Date("2026-09-01"),
    submittedBy: null,
    submittedByName: null,
    submittedAt: null,
    reviewedBy: null,
    reviewedByName: null,
    reviewedAt: null,
    reviewNote: null,
  };
}
function register(
  open?: ForecastAssumptionVersionRead,
  approved?: ForecastAssumptionVersionRead,
  asOf: string | null = "2026-09-30",
): ForecastAssumptionRegisterRead {
  return {
    bankId,
    asOf,
    openVersionId: open?.id ?? null,
    effectiveVersionId: asOf ? (approved?.id ?? null) : null,
    versions: [open, approved].filter(
      (v): v is ForecastAssumptionVersionRead => !!v,
    ),
  };
}
const mutations: { kind: string; bankId: string; variables: unknown }[] = [];
const mutation = (kind: string, bank: string) => ({
  isPending: false,
  error: null,
  mutate: (variables: unknown, options?: { onSuccess?: () => void }) => {
    mutations.push({ kind, bankId: bank, variables });
    options?.onSuccess?.();
  },
});
let requestedRunId: string | null = null;
let catalogue: ForecastRunSummaryRead[] = [];
let baseRun: ForecastRunRead | undefined;
const savedRuns = new Map<string, ForecastRunRead>();
const loader = NodeModule as typeof NodeModule & {
  _load: (request: string, parent: unknown, isMain: boolean) => unknown;
};
const originalLoad = loader._load;
function Surface({
  children,
  title,
  subtitle,
  action,
  actions,
}: {
  children?: ReactNode;
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section>
      <h3>{title}</h3>
      <p>{subtitle}</p>
      {action}
      {actions}
      {children}
    </section>
  );
}
loader._load = (request, parent, isMain) => {
  if (request === "@aequoros/risk-service-api") return {};
  if (request === "next/navigation")
    return { useSearchParams: () => ({ get: () => requestedRunId }) };
  if (request === "next/link") return { __esModule: true, default: Surface };
  if (request === "@/lib/api/hooks")
    return {
      useCreateForecastAssumptionVersion: (bank: string) =>
        mutation("create", bank),
      useUpdateForecastAssumptionVersion: (bank: string) =>
        mutation("update", bank),
      useSubmitForecastAssumptionVersion: (bank: string) =>
        mutation("submit", bank),
      useDecideForecastAssumptionVersion: (bank: string) =>
        mutation("decide", bank),
      useForecastRuns: () => ({ data: { runs: catalogue } }),
      useForecastRun: (_bank: string, id: string) => ({
        data: savedRuns.get(id),
      }),
      useForecastAssumptionRegister: () => ({}),
      useForecastScenarios: () => ({
        data: {
          assumptionVersion: null,
          scenarios: [],
          defaults: {
            feeIncomePctAssets: "1",
            taxRatePct: "25",
            securitiesShiftPp: "0",
          },
        },
      }),
    };
  if (request === "@/components/shell/BankContext")
    return {
      useBankContext: () => ({
        bank: { id: bankId },
        moduleScope: {
          forecastingAggregatedView: true,
          forecastingConfidentialView: true,
        },
      }),
    };
  if (request === "@/components/forecasting/hooks")
    return {
      useScenarioRunSet: () => ({ base: baseRun, isLoading: false }),
    };
  if (
    request.startsWith("@/components/ui/") ||
    request.startsWith("@/components/forecasting/charts/")
  )
    return {
      __esModule: true,
      default: Surface,
      SkeletonChart: Surface,
      ErrorPanel: Surface,
    };
  if (request === "@/lib/svgChartPalette")
    return { cssSeriesColor: () => "black" };
  if (request.startsWith("@/"))
    return originalLoad(
      path.resolve(__dirname, "../..", request.slice(2)),
      parent,
      isMain,
    );
  return originalLoad(request, parent, isMain);
};
function text(node: ReactTestInstance): string {
  return node.children
    .map((child) => (typeof child === "string" ? child : text(child)))
    .join("");
}
let renderer!: ReturnType<typeof create>;
function click(label: string) {
  const button = renderer.root
    .findAllByType("button")
    .find((b) => text(b) === label);
  assert.ok(button, `Missing button: ${label}`);
  assert.equal(button.props.disabled === true, false);
  act(() => button.props.onClick());
}
function change(node: ReactTestInstance, value: string) {
  act(() => node.props.onChange({ target: { value } }));
}
function submit() {
  act(() =>
    renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }),
  );
}
try {
  const { default: AssumptionRegister } =
    require("./AssumptionRegister") as typeof import("./AssumptionRegister");
  const renderRegister = (
    data: ForecastAssumptionRegisterRead,
    bank = bankId,
  ) => <AssumptionRegister bankId={bank} register={data} canEdit canApprove />;
  const mount = (data: ForecastAssumptionRegisterRead) => {
    mutations.length = 0;
    act(() => {
      renderer = create(renderRegister(data));
    });
  };
  const update = (data: ForecastAssumptionRegisterRead, bank = bankId) => {
    act(() => renderer.update(renderRegister(data, bank)));
  };
  const unmount = () => act(() => renderer.unmount());

  mount(register(version(1)));
  click("Edit draft");
  change(renderer.root.findByType("textarea"), "Unsaved V1 note");
  change(
    renderer.root.findByProps({ "aria-label": "Base case Loan growth" }),
    "7",
  );
  change(renderer.root.findByProps({ type: "date" }), "2026-08-01");
  update(register(version(1)));
  assert.equal(
    renderer.root.findByType("textarea").props.value,
    "Unsaved V1 note",
  );
  submit();
  assert.deepEqual(mutations.at(-1), {
    kind: "update",
    bankId,
    variables: {
      versionId: "version-1",
      payload: {
        effectiveFrom: "2026-08-01",
        changeNote: "Unsaved V1 note",
        presets: {
          base: { ...drivers, loanGrowthPct: "7" },
          adverse: drivers,
          severelyAdverse: drivers,
        },
      },
    },
  });
  assert.equal(renderer.root.findAllByType("form").length, 0);
  unmount();

  for (const next of [
    register(version(2)),
    register(version(1, "submitted")),
    register(undefined, version(1, "approved")),
    register(),
  ]) {
    mount(register(version(1)));
    click("Edit draft");
    change(renderer.root.findByType("textarea"), "Stale V1 note");
    update(next);
    assert.equal(renderer.root.findAllByType("form").length, 0);
    assert.equal(mutations.length, 0);
    if (next.openVersionId === "version-2") {
      click("Edit draft");
      assert.equal(
        renderer.root.findByType("textarea").props.value,
        "Proposal 2",
      );
      submit();
      assert.equal(
        (mutations.at(-1)?.variables as { versionId: string }).versionId,
        "version-2",
      );
    }
    unmount();
  }

  for (const [next, bank] of [
    [register(version(2)), bankId],
    [register(), "BK-BBBBBBBB"],
  ] as const) {
    mount(register());
    click("Propose new version");
    change(renderer.root.findByType("textarea"), "New proposal note");
    update(next, bank);
    assert.equal(renderer.root.findAllByType("form").length, 0);
    assert.equal(mutations.length, 0);
    unmount();
  }

  mount(register(undefined, version(1, "approved")));
  click("Propose new version");
  change(renderer.root.findByType("textarea"), "Backdated correction");
  change(renderer.root.findByProps({ type: "date" }), "2026-08-01");
  assert.match(
    text(renderer.root),
    /latest effective-from date on or before its book date/,
  );
  submit();
  assert.equal(mutations.at(-1)?.kind, "create");
  assert.equal(
    (
      mutations.at(-1)?.variables as { effectiveFrom: Date }
    ).effectiveFrom.toISOString(),
    "2026-08-01T00:00:00.000Z",
  );
  unmount();

  mount(register(version(1)));
  click("Submit for approval");
  assert.deepEqual(mutations.at(-1), {
    kind: "submit",
    bankId,
    variables: "version-1",
  });
  unmount();
  for (const decision of ["Approve", "Reject"] as const) {
    mount(register(version(1, "submitted")));
    change(renderer.root.findByType("textarea"), "V1 decision");
    update(register(version(1, "submitted")));
    assert.equal(
      renderer.root.findByType("textarea").props.value,
      "V1 decision",
    );
    update(register(version(2, "submitted")));
    assert.equal(renderer.root.findByType("textarea").props.value, "");
    assert.equal(
      renderer.root.findAllByType("button").find((b) => text(b) === "Reject")
        ?.props.disabled,
      true,
    );
    change(renderer.root.findByType("textarea"), "V2 decision");
    click(decision);
    assert.deepEqual(mutations.at(-1), {
      kind: "decide",
      bankId,
      variables: {
        versionId: "version-2",
        decision: decision.toLowerCase(),
        payload: { note: "V2 decision" },
      },
    });
    unmount();
  }

  mount(register(undefined, version(1, "approved"), null));
  let banner = text(renderer.root.findByProps({ role: "status" }));
  assert.match(banner, /Forecasting needs a book date/);
  assert.match(banner, /Ingest a book/);
  assert.doesNotMatch(
    banner,
    /No approved forecast assumptions|drafted, submitted/,
  );
  update(register(undefined, undefined, null));
  banner = text(renderer.root.findByProps({ role: "status" }));
  assert.match(banner, /Forecasting needs a book date/);
  assert.match(banner, /No approved forecast assumptions exist yet/);
  update({
    ...register(undefined, version(1, "approved")),
    effectiveVersionId: null,
  });
  banner = text(renderer.root.findByProps({ role: "status" }));
  assert.match(banner, /in force for/);
  assert.match(banner, /effective on or before this book date/);
  assert.doesNotMatch(banner, /drafted, submitted/);
  update(register());
  assert.match(
    text(renderer.root.findByProps({ role: "status" })),
    /drafted, submitted and approved by a second person/,
  );
  update(register(undefined, version(1, "approved")));
  assert.equal(renderer.root.findAllByProps({ role: "status" }).length, 0);
  assert.match(text(renderer.root), /Approved assumptions in force/);
  unmount();

  const { default: NiiPage } =
    require("../../app/(app)/forecasting/nii/page") as typeof import("../../app/(app)/forecasting/nii/page");
  function run(id: string, scenarioCode: "base" | "custom"): ForecastRunRead {
    return {
      id,
      status: "succeeded",
      scenarioCode,
      reportingPeriodId: "period-1",
      assumptionVersion: null,
      assumptions: drivers,
      createdAt: new Date("2026-09-30"),
      path: [
        { year: 0, periodLabel: "2026-09", nii: "90" },
        {
          year: 1,
          periodLabel: "2027-09",
          nii: "100",
          fees: "20",
          opex: "50",
          creditLosses: "5",
          netIncome: "65",
        },
      ],
    } as unknown as ForecastRunRead;
  }
  const oldRun = run("old-custom-run", "custom");
  baseRun = run("new-base-run", "base");
  savedRuns.set(oldRun.id, oldRun);
  savedRuns.set(baseRun.id, baseRun);
  catalogue = Array.from({ length: 50 }, (_, i) => ({
    ...baseRun,
    id: i ? `run-${i}` : baseRun!.id,
    periodLabel: "2026-09",
  })) as unknown as ForecastRunSummaryRead[];
  requestedRunId = oldRun.id;
  act(() => {
    renderer = create(<NiiPage />);
  });
  let picker = renderer.root.findByType("select");
  assert.equal(picker.props.value, oldRun.id);
  assert.equal(
    picker.findAllByType("option").filter((o) => o.props.value === oldRun.id)
      .length,
    1,
  );
  assert.match(
    text(
      picker.findAllByType("option").find((o) => o.props.value === oldRun.id)!,
    ),
    /Custom · 2026-09 · old-cust/,
  );
  assert.match(text(renderer.root), /Reading run old-cust/);
  change(picker, baseRun.id);
  picker = renderer.root.findByType("select");
  assert.equal(picker.props.value, baseRun.id);
  assert.equal(picker.findAllByType("option").length, 50);
  assert.match(text(renderer.root), /Reading run new-base/);
  unmount();

  requestedRunId = null;
  catalogue = [];
  act(() => {
    renderer = create(<NiiPage />);
  });
  picker = renderer.root.findByType("select");
  assert.equal(picker.props.value, baseRun.id);
  assert.equal(picker.findAllByType("option")[0].props.value, baseRun.id);
  unmount();

  const { default: AssumptionsPage } =
    require("../../app/(app)/forecasting/assumptions/page") as typeof import("../../app/(app)/forecasting/assumptions/page");
  act(() => {
    renderer = create(<AssumptionsPage />);
  });
  assert.match(
    text(renderer.root),
    /No approved assumption version covers the catalogue date/,
  );
  assert.match(
    text(renderer.root),
    /effective on or before the run's book date/,
  );
  unmount();
} finally {
  loader._load = originalLoad;
}
console.log(
  "Forecasting review regressions: proposal state, run picker, readiness and effective dating passed",
);
