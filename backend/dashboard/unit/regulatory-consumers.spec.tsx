// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import {
  IrrDashboardReadFromJSON,
  LiquidityDashboardReadFromJSON,
  ReturnTemplateReadFromJSON,
  RegulatoryRunReadFromJSON,
  CapitalDashboardReadFromJSON,
} from "@aequoros/risk-service-api";
import type { SdiLiquidityPosition } from "@/components/basel/sdiHooks";
import SdiLiquidityMonitoringView from "@/components/liquidity/SdiLiquidityMonitoringView";
import InstrumentsRegister from "@/components/submissions/InstrumentsRegister";
import SdiLiquiditySummary from "@/components/home/SdiLiquiditySummary";
import SdiLiquidityView from "@/components/liquidity/SdiLiquidityView";
import RiskPage from "@/app/(app)/risk/page";
import AlertsPage from "@/app/(app)/alerts/page";
import BreachBanner from "@/components/home/BreachBanner";
import PulseWall from "@/components/home/PulseWall";
import { usePulseCards } from "@/components/home/pulse";
import LiveStatusCard from "@/components/live/LiveStatusCard";
import UnifiedBell from "@/components/shell/UnifiedBell";
import RatioTrendChart from "@/components/home/RatioTrendChart";
import WindowAnalysis from "@/components/home/WindowAnalysis";
import BoardPackPage from "@/app/(app)/reports/board-pack/page";
import { SdiLiquidityNotAssessed } from "@/components/stress/EnterpriseStressWorkbench";
import SdiLiquidityBoardDisclosure from "@/components/reports/SdiLiquidityBoardDisclosure";
import LiquidityLayout from "@/app/(app)/liquidity/layout";
import LiquidityCockpit from "@/app/(app)/liquidity/page";
import CfpPage from "@/app/(app)/liquidity/cfp/page";
import MonitoringPage from "@/app/(app)/liquidity/monitoring/page";
import { irrLimits, liquidityLimits } from "@/components/risk/limits";
import { isRuleInForce } from "@/lib/regulatoryInstruments";
import type { BiEChartsOption } from "@/components/bi/EChart";

beforeAll(() =>
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
);
afterAll(() => vi.unstubAllGlobals());

const state = vi.hoisted(() => ({
  status: "exposure_draft",
  sdi: true,
  preparing: false,
  onlyIrr: false,
  capitalAlert: false,
  path: "/liquidity",
  sourceBank: "",
  breached: true,
}));
function scope() {
  return {
    institutionClass: state.sdi ? "sdi" : "bank",
    modules: new Set(
      state.onlyIrr ? ["irrbb"] : ["liquidity", "capital", "irrbb"],
    ),
    organizationModules: new Set(),
    hasInstitutionAuthority: true,
    isResolved: true,
    liquidityAggregatedView: !state.onlyIrr,
    liquidityConfidentialView: !state.onlyIrr,
    capitalAggregatedView: !state.onlyIrr,
    irrbbAggregatedView: true,
    irrbbConfidentialView: true,
    riskConfidentialView: true,
    institutionCapabilities: state.onlyIrr
      ? []
      : [
          {
            module: "cap",
            sensitivity: "aggregated",
            permission: "view",
            dataScope: { kind: "all" },
            requiresContextualAuthorization: false,
          },
        ],
  };
}
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-SYNTH001", name: "Synthetic institution" },
    period: { id: "period", periodEnd: new Date("2026-01-31") },
    moduleScope: scope(),
  }),
  useModuleScope: () => scope(),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(state.preparing ? "preparing=1" : ""),
  usePathname: () => state.path,
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/components/bi/InsightStrip", () => ({
  LandingInsightStrip: () => null,
  useKpiExplain: () => ({ explainFor: () => undefined, drawer: null }),
}));
vi.mock("@/components/bi/EChart", () => ({
  default: ({
    option,
    ariaLabel,
  }: {
    option: BiEChartsOption;
    ariaLabel?: string;
  }) => (
    <div role="img" aria-label={ariaLabel ?? "Assessment chart"}>
      {((option.series as { name?: string }[]) ?? []).map((series, index) => (
        <span key={index}>{series.name}</span>
      ))}
    </div>
  ),
}));
const query = <T,>(data: T) => ({
  data,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null,
  refetch: vi.fn(),
});
function sdiPosition(): SdiLiquidityPosition {
  return {
    as_of: "2026-01-31",
    ratios: [
      {
        code: "synthetic",
        label: "Synthetic LMTD ratio",
        value_pct: "12",
        threshold_pct: "10",
        status: state.breached ? "below_minimum" : "ok",
        threshold_source: "synthetic",
      },
    ],
    reserves: [
      {
        code: "primary_liquidity_reserve",
        label: "Synthetic reserve",
        value_pct: "20",
        threshold_pct: "10",
        status: "ok",
        source_citation: "Synthetic reserve rules",
        confirmation_status: "confirmed",
      },
    ],
    readiness: [
      { module: "liquidity_table1", status: "ready", reasons: [] },
      { module: "maturity_ladder", status: "ready", reasons: [] },
    ],
    maturity_ladder: [],
    funding_concentration: {
      providers: [],
      total_deposits_ghs: "0",
      top_five_deposits_ghs: "0",
      top_five_pct: "0",
      unattributed_deposits_ghs: "0",
    },
    counterbalancing_capacity: {
      gross_unencumbered_ghs: "0",
      monetized_value_ghs: "0",
      bog_eligible_ghs: "0",
      uncalibrated_asset_count: 0,
    },
  };
}
function irrDashboard() {
  return IrrDashboardReadFromJSON({
    bank: {},
    period: { id: "period" },
    metrics: {
      eve_status: "green",
      worst_eve_change_pct_tier1: "5",
      eve_limit_pct: "15",
      worst_scenario_code: "parallel_up_200",
    },
    eve_scenarios: [],
    gap_table: [],
    trend: [],
    validations: [],
  });
}
function liquidityDashboard() {
  return LiquidityDashboardReadFromJSON({
    bank: {},
    period: { id: "period", period_end: "2026-01-31" },
    metrics: {
      lcr_pct: "105",
      nsfr_pct: "110",
      lcr_status: "green",
      nsfr_status: "green",
    },
    trend: [
      {
        reporting_period_id: "period",
        period_end: "2026-01-31",
        label: "Jan",
        lcr_pct: "105",
        nsfr_pct: "110",
        stored: true,
      },
    ],
    asf: [],
    rsf: [],
    hqla_composition: [],
    outflows: [],
    inflows: [],
    nsfr_assets: [],
    nsfr_funding: [],
    validations: [],
  });
}
function capitalDashboard() {
  return CapitalDashboardReadFromJSON({
    bank: {},
    period: { id: "period" },
    buffers: {
      car_min_pct: "10",
      car_early_warning_pct: "12",
      headroom_pp: "4",
      car_early_warning_label: "Synthetic warning",
    },
    metrics: {
      car_pct: "14",
      car_status: "green",
      tier1_status: "green",
      cet1_status: "green",
      leverage_status: "green",
    },
    trend: [
      {
        reporting_period_id: "period",
        period_end: "2026-01-31",
        label: "Jan",
        car_pct: "14",
        stored: true,
      },
    ],
    validations: [],
    rwa_breakdown: [],
    capital_components: [],
  });
}
function run() {
  return RegulatoryRunReadFromJSON({
    inputs: {
      parameters: { thresholds_pct: { lcr_min: "100", nsfr_min: "100" } },
    },
    line_items: [],
    metric_results: [],
    validations: [],
    results: {},
    status: "succeeded",
    created_at: "2026-01-31",
  });
}
function alerts() {
  const items = [
    {
      module: "irr",
      message: "Synthetic IRR alert",
      findingId: "irr",
      severity: "high",
      createdAt: new Date(),
      ruleId: "irr",
      metric: null,
    },
    {
      module: "liquidity",
      message: "Synthetic LCR alert",
      findingId: "liquidity",
      severity: "critical",
      createdAt: new Date(),
      ruleId: "lcr",
      metric: null,
    },
    ...(state.capitalAlert
      ? [
          {
            module: "capital",
            message: "Synthetic capital alert",
            findingId: "capital",
            severity: "critical",
            createdAt: new Date(),
            ruleId: "car",
            metric: null,
          },
        ]
      : []),
  ];
  return {
    items,
    total: items.length,
    bySeverity: { critical: 1 + Number(state.capitalAlert), high: 1 },
    byModule: {
      irr: 1,
      liquidity: 1,
      ...(state.capitalAlert ? { capital: 1 } : {}),
    },
  };
}
function summary() {
  return {
    bankId: "BK-SYNTH001",
    computedAt: new Date(),
    sourceAsOfDate: new Date("2026-01-31T00:00:00Z"),
    isStale: false,
    modules: ["liquidity", "irr", "capital"].map((module) => ({
      module,
      status: "green",
      computedAt: new Date(),
      metrics: {
        lcr_pct: "105",
        worst_eve_change_pct_tier1: "5",
        car_pct: "14",
      },
      computedFromInputHash: "a".repeat(64),
      calculationGeneration: 1,
      pipelineState: "ready",
      sourceAsOfDate: new Date("2026-01-31T00:00:00Z"),
      sourceFactPeriodId: "period",
      engineVersion: "synthetic",
    })),
  };
}
vi.mock("@/lib/api/hooks", () => ({
  useReturnTemplates: (bankId: string) => {
    state.sourceBank = bankId;
    return query({
      templates: [
        "LMT",
        "SDI-LMT-MONTHLY",
        "IRRBB-PILOT",
        "SDI-IRRBB-QUARTERLY",
        "LCR-NSFR",
      ].map((code) =>
        ReturnTemplateReadFromJSON({
          code,
          instrument_status: state.status,
          title: code,
        }),
      ),
    });
  },
  useBankAlerts: () => query(alerts()),
  useLiveSummary: () => query(summary()),
  useIrrDashboard: () => query(irrDashboard()),
  useLiquidityDashboard: () => query(liquidityDashboard()),
  useCapitalDashboard: () =>
    query(state.sdi || state.onlyIrr ? undefined : capitalDashboard()),
  useFxDashboard: () => query(undefined),
  useFtpDashboard: () => query(undefined),
  useRegulatoryRun: () => query(run()),
  useLiveSnapshots: () => query({ snapshots: [] }),
  useEffectiveRatioDashboards: () => ({
    liquidity: query(liquidityDashboard()),
    capital: query(capitalDashboard()),
  }),
  useCashflowWindow: () => query(undefined),
  useWindowAnalytics: () =>
    query({
      periodCount: 1,
      ratios: [
        {
          ratio: "lcr_pct",
          points: [],
          startValue: "105",
          endValue: "105",
          change: "0",
          avg: "105",
          min: "105",
          max: "105",
        },
        {
          ratio: "car_pct",
          points: [],
          startValue: "14",
          endValue: "14",
          change: "0",
          avg: "14",
          min: "14",
          max: "14",
        },
      ],
      daily: [
        {
          module: "irr",
          metricKey: "worst_eve_change_pct_tier1",
          avg: "5",
          min: "5",
          dayCount: 1,
        },
      ],
    }),
  useNotifications: () => query({ notifications: [], unreadCount: 0 }),
  useMarkNotificationRead: () => ({ mutate: vi.fn() }),
  useMarkAllNotificationsRead: () => ({ mutate: vi.fn() }),
  useEwiDashboard: () => query({ indicators: [], escalationState: "normal" }),
  useCfpSummary: () => query({ approved: null }),
  useCfpEvents: () => query({ events: [] }),
  useLiquidityThresholdRegister: () => query({ thresholds: [] }),
  useLiquidityHaircutSchedule: () => query({ haircuts: [] }),
}));
vi.mock("@/components/basel/sdiHooks", () => ({
  useSdiLiquidityPosition: () => query(sdiPosition()),
  useSdiCapitalSummary: () => query(undefined),
  useSdiCapitalChecks: () => query({ checks: [] }),
  useSdiLargeExposures: () => query({ exposures: [] }),
  useLiquidityMonitoring: () =>
    query({
      funding_concentration: sdiPosition().funding_concentration,
      counterbalancing_capacity: sdiPosition().counterbalancing_capacity,
      maturity_ladder: [],
      readiness: [],
    }),
}));
afterEach(() => {
  cleanup();
  state.status = "exposure_draft";
  state.sdi = true;
  state.preparing = false;
  state.onlyIrr = false;
  state.capitalAlert = false;
  state.path = "/liquidity";
  state.breached = true;
});

it.each([
  undefined,
  "exposure_draft",
  "final_not_in_force",
  "unpublished",
  "in_force",
] as const)("uses resolved legal evidence for %s", (status) => {
  expect(isRuleInForce(status ? { instrumentStatus: status } : undefined)).toBe(
    status === "in_force",
  );
});
it("keeps draft ratios and their readiness counts out of the Home SDI summary", () => {
  const view = render(<SdiLiquiditySummary bankId="BK-SYNTH001" />);
  expect(screen.queryByText("Table 1 breaches")).toBeNull();
  expect(screen.getByText("Reserve breaches")).toBeVisible();
  expect(screen.getByText("1 / 1")).toBeVisible();
  state.status = "in_force";
  view.rerender(<SdiLiquiditySummary bankId="BK-SYNTH001" />);
  expect(screen.getByText("Table 1 breaches")).toBeVisible();
  expect(screen.getByText("2 / 2")).toBeVisible();
});
it("keeps draft LMTD ratios out of Risk's wall and compliant count", () => {
  state.breached = false;
  const view = render(<RiskPage />);
  expect(screen.queryByText("Synthetic LMTD ratio")).toBeNull();
  expect(screen.getByText("Synthetic reserve")).toBeVisible();
  expect(
    within(
      screen.getByText("Compliant", { selector: "p" }).closest(".card")!,
    ).getByText("1"),
  ).toBeVisible();
  state.status = "in_force";
  view.rerender(<RiskPage />);
  expect(screen.getByText("Synthetic LMTD ratio")).toBeVisible();
  expect(
    within(
      screen.getByText("Compliant", { selector: "p" }).closest(".card")!,
    ).getByText("2"),
  ).toBeVisible();
});
it("excludes draft IRRBB from Risk validation checks and limits", () => {
  state.sdi = false;
  render(<RiskPage />);
  expect(screen.queryByText("Worst ΔEVE / Tier 1")).toBeNull();
  fireEvent.click(screen.getByText("Validation checks"));
  expect(
    screen.queryByRole("heading", { name: "Interest Rate Risk" }),
  ).toBeNull();
});
it("suppresses both pipeline and diagnostic draft alerts", () => {
  state.capitalAlert = true;
  const view = render(<AlertsPage />);
  expect(screen.queryByText("Synthetic IRR alert")).toBeNull();
  expect(screen.queryByText("Synthetic LMTD ratio")).toBeNull();
  expect(screen.getByText("Synthetic capital alert")).toBeVisible();
  expect(
    within(screen.getByText("Open alerts").closest(".card")!).getByText("1"),
  ).toBeVisible();
  state.status = "in_force";
  view.rerender(<AlertsPage />);
  expect(screen.getByText("Synthetic IRR alert")).toBeVisible();
  expect(screen.getByText("Synthetic LMTD ratio")).toBeVisible();
});
function PulseProbe() {
  const pulse = usePulseCards("BK-SYNTH001", true);
  return (
    <div>
      {pulse.modules.join(",")}
      <span>{pulse.cards.irr.metricLabel ?? "No IRR assessment"}</span>
    </div>
  );
}
it("does not publish a draft IRR metric from the Home pulse model", () => {
  state.onlyIrr = true;
  const view = render(<PulseProbe />);
  expect(screen.getByText("No IRR assessment")).toBeVisible();
  state.status = "in_force";
  view.rerender(<PulseProbe />);
  expect(screen.getByText("Worst ΔEVE / Tier 1")).toBeVisible();
});
it("omits draft and reference cards from the Home pulse wall", () => {
  state.sdi = false;
  state.status = "unpublished";
  render(<PulseWall bankId="BK-SYNTH001" moduleOrder={["liquidity", "irr"]} />);
  expect(screen.queryByText("Worst ΔEVE / Tier 1")).toBeNull();
  expect(screen.queryByText("Liquidity Coverage Ratio")).toBeNull();
});
it("does not affirm compliance or alert on a draft-only Home assessment", () => {
  state.onlyIrr = true;
  const view = render(<BreachBanner bankId="BK-SYNTH001" />);
  expect(screen.queryByText("All limits compliant")).toBeNull();
  expect(screen.queryByText("Synthetic IRR alert")).toBeNull();
  expect(screen.getByText("Limit compliance not assessed")).toBeVisible();
  state.status = "in_force";
  view.rerender(<BreachBanner bankId="BK-SYNTH001" />);
  expect(screen.getByText("Synthetic IRR alert")).toBeVisible();
});
it("filters live status tiles and breach counts by legal status", () => {
  state.sdi = false;
  state.capitalAlert = true;
  render(<LiveStatusCard />);
  expect(screen.queryByText("Interest Rate Risk")).toBeNull();
  expect(screen.queryByText("Liquidity Coverage Ratio")).toBeNull();
  expect(screen.getByText("1 breach")).toBeVisible();
});
it("does not put draft alerts in the header badge or stream", () => {
  state.capitalAlert = true;
  render(<UnifiedBell />);
  fireEvent.click(screen.getByRole("button", { name: "Notifications (1)" }));
  expect(screen.getByText("Synthetic capital alert")).toBeVisible();
  expect(screen.queryByText("Synthetic IRR alert")).toBeNull();
});
it("omits reference liquidity from the Home trend's series and counts", () => {
  state.sdi = false;
  state.status = "unpublished";
  const view = render(
    <RatioTrendChart bankId="BK-SYNTH001" periodId="period" />,
  );
  expect(screen.queryByText("LCR")).toBeNull();
  expect(screen.queryByText("NSFR")).toBeNull();
  expect(screen.getByRole("img", { name: /CAR across/ })).toBeVisible();
  state.status = "in_force";
  view.rerender(<RatioTrendChart bankId="BK-SYNTH001" periodId="period" />);
  expect(screen.getByText("LCR")).toBeVisible();
  expect(screen.getByText("NSFR")).toBeVisible();
});
it("excludes reference liquidity and draft IRR from window analytics", () => {
  state.sdi = false;
  render(<WindowAnalysis bankId="BK-SYNTH001" />);
  fireEvent.change(screen.getByLabelText("Window start date"), {
    target: { value: "2026-01-01" },
  });
  fireEvent.change(screen.getByLabelText("Window end date"), {
    target: { value: "2026-01-31" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Compute" }));
  expect(screen.queryByText("LCR")).toBeNull();
  expect(screen.queryByText(/ΔEVE/)).toBeNull();
  expect(screen.getByText("CAR")).toBeVisible();
});
it("does not export reference or draft live modules in the board pack", () => {
  state.sdi = false;
  render(<BoardPackPage />);
  expect(screen.queryByText("Interest Rate Risk")).toBeNull();
  expect(screen.queryByText("Liquidity Coverage Ratio")).toBeNull();
});
it("retains reserves while excluding draft baseline ratios from stress evidence", () => {
  const view = render(<SdiLiquidityNotAssessed position={sdiPosition()} />);
  expect(screen.getByText(/Reserve evidence as of/)).toBeVisible();
  expect(screen.queryByText(/Table 1/)).toBeNull();
  state.status = "in_force";
  view.rerender(<SdiLiquidityNotAssessed position={sdiPosition()} />);
  expect(screen.getByText(/Table 1 assessment/)).toBeVisible();
});
it("excludes draft LMTD from the stress board-pack disclosure", () => {
  const view = render(
    <SdiLiquidityBoardDisclosure
      asOf="2026-01-31"
      table1Breaches={1}
      reserveBreaches={0}
    />,
  );
  expect(screen.queryByText(/Table 1/)).toBeNull();
  expect(screen.getByText(/Reserve evidence/)).toBeVisible();
  state.status = "in_force";
  view.rerender(
    <SdiLiquidityBoardDisclosure
      asOf="2026-01-31"
      table1Breaches={1}
      reserveBreaches={0}
    />,
  );
  expect(screen.getByText(/Table 1 assessment/)).toBeVisible();
});
it("uses legal evidence in pure Risk limit extraction", () => {
  expect(irrLimits(irrDashboard())).toEqual([]);
  expect(
    irrLimits(irrDashboard(), { instrumentStatus: "exposure_draft" }),
  ).toEqual([]);
  expect(
    irrLimits(irrDashboard(), { instrumentStatus: "in_force" }),
  ).toHaveLength(1);
  expect(
    liquidityLimits(liquidityDashboard(), run(), {
      instrumentStatus: "unpublished",
    }),
  ).toEqual([]);
  expect(
    liquidityLimits(liquidityDashboard(), run(), {
      instrumentStatus: "in_force",
    }),
  ).toHaveLength(2);
});
it("keeps draft ratios in an explicit SDI preparation cockpit", () => {
  state.preparing = true;
  render(<SdiLiquidityView bankId="BK-SYNTH001" />);
  expect(screen.getByText("Synthetic LMTD ratio")).toBeVisible();
  expect(screen.getByText("Draft")).toBeVisible();
  expect(state.sourceBank).toBe("BK-SYNTH001");
});
it("preserves liquidity context through the cockpit controls", () => {
  state.sdi = false;
  state.preparing = true;
  render(
    <LiquidityLayout>
      <LiquidityCockpit />
    </LiquidityLayout>,
  );
  expect(
    screen.getByRole("link", { name: "Thresholds and maturity monitoring" }),
  ).toHaveAttribute("href", "/liquidity/monitoring?preparing=1");
});
it("preserves preparation on the CFP monitoring link", () => {
  state.sdi = false;
  state.preparing = true;
  state.path = "/liquidity/cfp";
  render(
    <LiquidityLayout>
      <CfpPage />
    </LiquidityLayout>,
  );
  expect(
    screen.getByRole("link", { name: "Monitoring tools & threshold register" }),
  ).toHaveAttribute("href", "/liquidity/monitoring?preparing=1");
});
it("selects LMT explicitly from monitoring's reporting link", () => {
  state.sdi = false;
  state.preparing = true;
  state.path = "/liquidity/monitoring";
  render(
    <LiquidityLayout>
      <MonitoringPage />
    </LiquidityLayout>,
  );
  expect(
    screen.getByRole("link", { name: "Regulatory Reporting" }),
  ).toHaveAttribute("href", "/submissions/returns?code=LMT&preparing=1");
  expect(screen.getByRole("link", { name: "Stress tab" })).toHaveAttribute(
    "href",
    "/liquidity/stress?preparing=1",
  );
});

it("shows SDI ratios only as deliberate monitoring preparation", () => {
  const view = render(
    <SdiLiquidityMonitoringView bankId="BK-SYNTH001" institutionClass="sdi" />,
  );
  expect(screen.queryByText("Worst LMTD ratio buffer")).toBeNull();
  expect(screen.queryByText("Synthetic LMTD ratio")).toBeNull();
  state.preparing = true;
  view.rerender(
    <SdiLiquidityMonitoringView bankId="BK-SYNTH001" institutionClass="sdi" />,
  );
  expect(screen.getByText("Worst LMTD ratio buffer")).toBeVisible();
  expect(
    screen.getByRole("cell", { name: "Synthetic LMTD ratio" }),
  ).toBeVisible();
});
it("offers deliberate reference monitoring from the instruments register", () => {
  state.sdi = false;
  state.status = "unpublished";
  render(<InstrumentsRegister />);
  const section = screen
    .getByRole("heading", { name: "LCR-NSFR" })
    .closest("section")!;
  expect(
    within(section).getByRole("link", { name: "Open preparation workspace" }),
  ).toHaveAttribute("href", "/liquidity?preparing=1");
});

it("keeps the ordinary CFP accessible without unrelated Basel reference evidence", () => {
  state.sdi = false;
  state.status = "unpublished";
  state.path = "/liquidity/cfp";
  render(
    <LiquidityLayout>
      <CfpPage />
    </LiquidityLayout>,
  );
  expect(
    screen.getByRole("heading", { name: "Contingency Funding Plan" }),
  ).toBeVisible();
  expect(screen.queryByText("Reference")).toBeNull();
});
it("keeps the SDI reserve cockpit accessible without bank-only evidence", () => {
  state.status = "unpublished";
  render(
    <LiquidityLayout>
      <LiquidityCockpit />
    </LiquidityLayout>,
  );
  expect(screen.getByText("Synthetic reserve")).toBeVisible();
  expect(screen.queryByText("Reference")).toBeNull();
  expect(screen.queryByText("Synthetic LMTD ratio")).toBeNull();
});
