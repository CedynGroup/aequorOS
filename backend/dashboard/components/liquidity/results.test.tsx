import assert from "node:assert/strict";
import NodeModule from "node:module";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type {
  LiquidityDashboardRead,
  RegulatoryRunRead,
} from "@aequoros/risk-service-api";
import { formatFigure } from "../../lib/api/values";
import RatioGauge from "../ui/RatioGauge";
import LimitBar from "../ui/LimitBar";
import Sparkline from "../ui/Sparkline";
import type { ModuleScope } from "../../lib/modules";

const loader = NodeModule as typeof NodeModule & {
  _load: (request: string, parent: unknown, isMain: boolean) => unknown;
};
const originalLoad = loader._load;
const scope: ModuleScope = {
  modules: new Set(["liquidity"]),
  entitledModules: new Set(["liquidity"]),
  organizationModules: new Set(),
  hasInstitutionAuthority: true,
  institutionClass: "bank",
  isResolved: true,
  liquidityAggregatedView: true,
  liquidityConfidentialView: true,
};
let dashboard: LiquidityDashboardRead;
let chartOption: {
  series: { data: { value: number | null }[]; markLine?: unknown }[];
  yAxis: { min: number };
};
loader._load = (request, parent, isMain) => {
  if (request === "@/lib/api/client")
    return { isApiError: () => false, isModuleUnavailable: () => false };
  if (request === "@/lib/api/hooks")
    return new Proxy(
      {},
      {
        get: (_target, name) => () =>
          name === "useLiquidityDashboard"
            ? { data: dashboard }
            : name === "useBankAlerts"
              ? { data: { items: [], bySeverity: {} } }
              : {},
      },
    );
  if (request === "@/components/shell/BankContext")
    return {
      useBankContext: () => ({
        bank: { id: "fixture", name: "Fixture Bank" },
        moduleScope: scope,
      }),
      useModuleScope: () => scope,
    };
  if (request === "@/components/bi/InsightStrip")
    return {
      LandingInsightStrip: () => null,
      useKpiExplain: () => ({ explainFor: () => undefined, drawer: null }),
    };
  if (request === "@/components/bi/EChart")
    return {
      __esModule: true,
      default: ({ option }: { option: typeof chartOption }) => {
        chartOption = option;
        return <div />;
      },
    };
  if (request.startsWith("@/"))
    return originalLoad(
      path.resolve(__dirname, "../..", request.slice(2)),
      parent,
      isMain,
    );
  return originalLoad(request, parent, isMain);
};

const Page = require("../../app/(app)/liquidity/nsfr/page")
  .default as typeof import("../../app/(app)/liquidity/nsfr/page").default;
const Trend = require("./charts/RatioTrendChart").default;
const Outflow = require("./charts/NetOutflowChart").default;
const LimitWall = require("../risk/LimitWall")
  .default as typeof import("../risk/LimitWall").default;
const { liquidityLimits } =
  require("../risk/limits") as typeof import("../risk/limits");
const { usePulseCards } =
  require("../home/pulse") as typeof import("../home/pulse");

function readLiquidityPulse() {
  let pulse: ReturnType<typeof usePulseCards>["cards"]["liquidity"] | undefined;
  function PulseHarness() {
    pulse = usePulseCards("fixture", true).cards.liquidity;
    return null;
  }
  renderToStaticMarkup(<PulseHarness />);
  assert.ok(pulse);
  return pulse;
}

const PulseWall = require("../home/PulseWall")
  .default as typeof import("../home/PulseWall").default;
const BreachBanner = require("../home/BreachBanner")
  .default as typeof import("../home/BreachBanner").default;
const Buffer = require("../../app/(app)/liquidity/buffer/page")
  .default as typeof import("../../app/(app)/liquidity/buffer/page").default;
const Overview = require("../../app/(app)/liquidity/page")
  .default as typeof import("../../app/(app)/liquidity/page").default;
const fixture = (value: string | null) =>
  ({
    metrics: {
      lcrPct: value,
      nsfrPct: value,
      lcrStatus: value === null ? "na" : "red",
      nsfrStatus: value === null ? "na" : "red",
      hqlaTotalGhs: value,
      netOutflows30dGhs: value,
      asfTotalGhs: value,
      rsfTotalGhs: value,
    },
    asf: [],
    rsf: [],
    outflows: [],
    inflows: [],
    hqlaComposition: [],
    validations: [],
    period: { id: "current" },
    trend: [
      { reportingPeriodId: "previous", lcrPct: "123" },
      { reportingPeriodId: "current", lcrPct: value },
    ],
  }) as unknown as LiquidityDashboardRead;
const run = {
  inputs: {
    parameters: { thresholds_pct: { lcr_min: "100", nsfr_min: "100" } },
  },
} as unknown as RegulatoryRunRead;
const textOf = (markup: string) =>
  markup.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
try {
  assert.equal(
    formatFigure(null, (value) => `${value}%`),
    "Unavailable",
  );
  assert.equal(
    formatFigure("0", (value) => `${value}%`),
    "0%",
  );
  for (const Component of [RatioGauge, LimitBar]) {
    const props = {
      label: "Ratio",
      value: null,
      threshold: 100,
      limit: 100,
      status: "pending" as const,
    };
    const refused = renderToStaticMarkup(<Component {...props} />);
    assert.match(refused, /Unavailable/);
    assert.doesNotMatch(refused, /<svg|role="img"/);
    const measured = renderToStaticMarkup(<Component {...props} value={0} />);
    assert.doesNotMatch(measured, /Unavailable/);
    assert.match(measured, /role="img"/);
  }
  dashboard = fixture(null);
  const refusedPage = renderToStaticMarkup(<Page />);
  assert.match(refusedPage, /Funding surplus Unavailable/);
  assert.doesNotMatch(refusedPage, /0\.00%|GHS 0|Over by/);
  const refusedLimits = liquidityLimits(dashboard, run);
  assert.deepEqual(
    refusedLimits.map((row) => [row.value, row.status]),
    [
      [null, "na"],
      [null, "na"],
    ],
  );
  const wall = renderToStaticMarkup(<LimitWall rows={refusedLimits} />);
  assert.match(wall, /Pending/);
  assert.match(wall, /Unavailable/);
  assert.doesNotMatch(wall, /Compliant/);
  const refusedPulse = readLiquidityPulse();
  assert.equal(refusedPulse.value, "Unavailable");
  assert.equal(refusedPulse.delta, undefined);
  assert.deepEqual(refusedPulse.spark, [123, null]);
  dashboard = fixture("0");
  assert.match(renderToStaticMarkup(<Page />), /0\.00%/);
  assert.deepEqual(
    liquidityLimits(dashboard, run).map((row) => [row.value, row.status]),
    [
      [0, "crit"],
      [0, "crit"],
    ],
  );
  assert.equal(readLiquidityPulse().value, "0.00");
  for (const refusedFigure of ["lcr", "nsfr"] as const) {
    for (const staleLive of [false, true]) {
      dashboard = fixture("125");
      dashboard.metrics.lcrStatus = "green";
      dashboard.metrics.nsfrPct = "150";
      dashboard.metrics.nsfrStatus = "green";
      dashboard.metrics[`${refusedFigure}Pct`] = null;
      dashboard.metrics[`${refusedFigure}Status`] = "na";
      dashboard.trend[1][`${refusedFigure}Pct`] = null;
      if (staleLive)
        dashboard.live = {
          calculationGeneration: 1,
          status: "green",
          computedAt: new Date("2026-09-29"),
          computedFromInputHash: "fixture",
          engineVersion: "fixture",
          metrics: {},
          module: "liquidity",
          pipelineError: null,
          pipelineState: "ready",
          sourceAsOfDate: new Date("2026-09-29"),
          sourceFactPeriodId: "previous",
        };
      const pulse = readLiquidityPulse();
      assert.equal(pulse.status, "na");
      assert.equal(
        pulse.value,
        refusedFigure === "lcr" ? "Unavailable" : "125.00",
      );
      assert.equal(
        pulse.hint,
        refusedFigure === "lcr" ? "NSFR 150.00%" : "NSFR Unavailable",
      );
      const pulseWall = renderToStaticMarkup(
        <PulseWall bankId="fixture" moduleOrder={["liquidity"]} />,
      );
      assert.match(pulseWall, /Unavailable/);
      assert.doesNotMatch(pulseWall, /Compliant/);
      const banner = renderToStaticMarkup(<BreachBanner bankId="fixture" />);
      assert.match(banner, /Limit compliance not assessed/);
      assert.doesNotMatch(banner, /All limits compliant|modules computed/);
    }
  }
  dashboard = fixture(null);
  assert.match(
    renderToStaticMarkup(<BreachBanner bankId="fixture" />),
    /Limit compliance not assessed/,
  );
  dashboard = fixture("0");
  assert.equal(readLiquidityPulse().status, "red");
  assert.match(
    renderToStaticMarkup(
      <PulseWall bankId="fixture" moduleOrder={["liquidity"]} />,
    ),
    /Breach/,
  );
  assert.match(
    renderToStaticMarkup(<BreachBanner bankId="fixture" />),
    /breaching live limits/,
  );
  dashboard = fixture("125");
  dashboard.metrics.lcrStatus = "green";
  dashboard.metrics.nsfrStatus = "green";
  assert.match(
    renderToStaticMarkup(
      <PulseWall bankId="fixture" moduleOrder={["liquidity"]} />,
    ),
    /Compliant/,
  );
  assert.match(
    renderToStaticMarkup(<BreachBanner bankId="fixture" />),
    /All limits compliant/,
  );
  dashboard = fixture(null);
  dashboard.metrics.nsfrPct = "150";
  dashboard.metrics.nsfrStatus = "green";
  const refusedBuffer = textOf(renderToStaticMarkup(<Buffer />));
  assert.match(refusedBuffer, /Asset classes held Unavailable/);
  assert.match(refusedBuffer, /Buffer quality Unavailable/);
  assert.doesNotMatch(
    refusedBuffer,
    /Asset classes held 0|All Level 1|Includes/,
  );
  const refusedOverview = textOf(renderToStaticMarkup(<Overview />));
  assert.match(refusedOverview, /Largest HQLA concentration Unavailable/);
  assert.doesNotMatch(refusedOverview, /No HQLA instruments/);
  for (const empty of [true, false]) {
    for (const allLevel1 of [true, false]) {
      dashboard = fixture(empty ? "0" : "125");
      dashboard.validations = [
        {
          ruleCode: "hqla_all_level1",
          passed: allLevel1,
          message: "Synthetic buffer quality result",
          severity: "info",
        },
      ];
      if (!empty)
        dashboard.hqlaComposition = [
          {
            lineCode: "securities",
            description: "Synthetic securities",
            exposureAmount: "125",
            ratePct: "100",
            weightedAmount: "125",
          },
        ];
      const buffer = textOf(renderToStaticMarkup(<Buffer />));
      assert.match(buffer, new RegExp(`Asset classes held ${empty ? 0 : 1}`));
      assert.match(buffer, allLevel1 ? /All Level 1/ : /Includes &lt; Level 1/);
      assert.doesNotMatch(buffer, /Unavailable/);
      const overview = textOf(renderToStaticMarkup(<Overview />));
      if (empty) assert.match(overview, /No HQLA instruments/);
      else
        assert.match(
          overview,
          /Largest HQLA concentration 100\.0% Synthetic securities/,
        );
    }
  }
  const spark = renderToStaticMarkup(<Sparkline data={[1, null, 3, 4]} />);
  assert.match(spark, /d="M[^L]*M[^L]*L/);
  assert.match(
    renderToStaticMarkup(<Sparkline data={[null, 0, null]} />),
    /<circle/,
  );
  renderToStaticMarkup(
    <Trend
      data={[
        { label: "A", primary: 123, secondary: 150 },
        { label: "B", primary: null, secondary: 0 },
      ]}
      threshold={null}
      secondaryLabel="NSFR"
    />,
  );
  assert.deepEqual(
    chartOption!.series[0].data.map((point) => point.value),
    [123, null],
  );
  assert.deepEqual(
    chartOption!.series[1].data.map((point) => point.value),
    [150, 0],
  );
  renderToStaticMarkup(
    <Trend
      data={[{ label: "A", primary: null, secondary: 150 }]}
      threshold={null}
      secondaryLabel="NSFR"
    />,
  );
  assert.equal(chartOption!.yAxis.min, 145);
  renderToStaticMarkup(
    <Outflow outflows={[]} cappedInflows={null} netOutflows={null} />,
  );
  assert.deepEqual(chartOption!.series[0].data, [null, null]);
  assert.equal(chartOption!.series[0].markLine, undefined);
} finally {
  loader._load = originalLoad;
}
console.log(
  "liquidity results: nullable figures preserve absence and measured zeros",
);
