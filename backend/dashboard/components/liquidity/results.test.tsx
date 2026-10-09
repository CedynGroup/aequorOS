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

const loader = NodeModule as typeof NodeModule & {
  _load: (request: string, parent: unknown, isMain: boolean) => unknown;
};
const originalLoad = loader._load;
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
          name === "useLiquidityDashboard" ? { data: dashboard } : {},
      },
    );
  if (request === "@/components/shell/BankContext")
    return {
      useBankContext: () => ({ bank: { name: "Fixture Bank" } }),
      useModuleScope: () => ({ liquidityAggregatedView: true }),
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

const Page = require("../../app/(app)/liquidity/nsfr/page").default;
const Trend = require("./charts/RatioTrendChart").default;
const Outflow = require("./charts/NetOutflowChart").default;
const LimitWall = require("../risk/LimitWall")
  .default as typeof import("../risk/LimitWall").default;
const { liquidityLimits } =
  require("../risk/limits") as typeof import("../risk/limits");
const { usePulseCards } =
  require("../home/pulse") as typeof import("../home/pulse");
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
  const refusedPulse = usePulseCards("fixture", true).cards.liquidity;
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
  assert.equal(usePulseCards("fixture", true).cards.liquidity.value, "0.00");
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
