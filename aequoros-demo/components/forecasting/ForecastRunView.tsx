'use client';

/**
 * Shared renderer for a persisted 5-year forecast run — summary KPIs,
 * balance-sheet / ratio / net-income charts, the full annual path table, and
 * validations. Used by the Forecast Dashboard and the Scenario Builder.
 */

import type { ForecastRunRead, ProjectionYearRead } from '@aequoros/risk-service-api';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts';
import KPICard from '@/components/ui/KPICard';
import RunBadge from '@/components/ui/RunBadge';
import ValidationList from '@/components/ui/ValidationList';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import RatioHistoryChart from '@/components/charts/RatioHistoryChart';
import BalanceSheetProjectionChart from '@/components/charts/BalanceSheetProjectionChart';
import { num, statusTone } from '@/lib/api/values';
import { fmtCurrency, fmtPct } from '@/lib/format';

function metricStatus(run: ForecastRunRead, metricCode: string) {
  return (
    run.metricResults.find((m) => m.metricCode === metricCode)?.status ?? null
  );
}

function yearLabel(point: ProjectionYearRead): string {
  return point.year === 0 ? `Y0 · ${point.periodLabel}` : `Y${point.year}`;
}

const pathColumns: Column<ProjectionYearRead>[] = [
  {
    key: 'year',
    header: 'Year',
    width: '10%',
    render: (r) => (
      <span className="font-medium text-navy">
        Y{r.year}{' '}
        <span className="font-mono text-caption text-slate">{r.periodLabel}</span>
      </span>
    ),
  },
  {
    key: 'assets',
    header: 'Assets',
    numeric: true,
    render: (r) => fmtCurrency(num(r.totalAssets), 'GHS'),
  },
  {
    key: 'loans',
    header: 'Loans',
    numeric: true,
    render: (r) => fmtCurrency(num(r.loans), 'GHS'),
  },
  {
    key: 'deposits',
    header: 'Deposits',
    numeric: true,
    render: (r) => fmtCurrency(num(r.deposits), 'GHS'),
  },
  {
    key: 'equity',
    header: 'Equity',
    numeric: true,
    render: (r) => fmtCurrency(num(r.equity), 'GHS'),
  },
  {
    key: 'nii',
    header: 'NII',
    numeric: true,
    render: (r) => (r.year === 0 ? '—' : fmtCurrency(num(r.nii), 'GHS')),
  },
  {
    key: 'netIncome',
    header: 'Net income',
    numeric: true,
    render: (r) => (r.year === 0 ? '—' : fmtCurrency(num(r.netIncome), 'GHS')),
  },
  {
    key: 'dividends',
    header: 'Dividends',
    numeric: true,
    render: (r) => (r.year === 0 ? '—' : fmtCurrency(num(r.dividends), 'GHS')),
  },
  {
    key: 'roe',
    header: 'ROE',
    numeric: true,
    render: (r) => (r.roePct === null ? '—' : fmtPct(num(r.roePct), 2)),
  },
  {
    key: 'car',
    header: 'CAR',
    numeric: true,
    render: (r) => (
      <span className={num(r.carPct) < 10 ? 'text-critical font-medium' : undefined}>
        {fmtPct(num(r.carPct), 2)}
      </span>
    ),
  },
  {
    key: 'lcr',
    header: 'LCR',
    numeric: true,
    render: (r) => (
      <span className={num(r.lcrPct) < 100 ? 'text-critical font-medium' : undefined}>
        {fmtPct(num(r.lcrPct), 2)}
      </span>
    ),
  },
  {
    key: 'nsfr',
    header: 'NSFR',
    numeric: true,
    render: (r) => (
      <span className={num(r.nsfrPct) < 100 ? 'text-critical font-medium' : undefined}>
        {fmtPct(num(r.nsfrPct), 2)}
      </span>
    ),
  },
];

export default function ForecastRunView({ run }: { run: ForecastRunRead }) {
  const path = run.path;
  const balanceData = path.map((p) => ({
    month: yearLabel(p),
    loans: Math.round(num(p.loans) / 1_000_000),
    securities: Math.round(num(p.securities) / 1_000_000),
    cash: Math.round(num(p.cash) / 1_000_000),
  }));
  const netIncomeData = path
    .filter((p) => p.year > 0)
    .map((p) => ({
      month: yearLabel(p),
      netIncome: Math.round(num(p.netIncome) / 1_000_000),
    }));

  return (
    <div className="space-y-6">
      {/* Summary KPIs */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <KPICard
          label="Average ROE"
          value={num(run.summary.avgRoePct)}
          suffix="%"
          decimals={2}
          footer="5-year average return on equity"
        />
        <KPICard
          label="Year-5 CAR"
          value={num(run.summary.year5CarPct)}
          suffix="%"
          decimals={2}
          status={statusTone(metricStatus(run, 'year5_car_pct'))}
          footer="BoG minimum 10%"
        />
        <KPICard
          label="Year-5 LCR"
          value={num(run.summary.year5LcrPct)}
          suffix="%"
          decimals={2}
          status={statusTone(metricStatus(run, 'year5_lcr_pct'))}
          footer="BoG minimum 100%"
        />
        <KPICard
          label="Year-5 NSFR"
          value={num(run.summary.year5NsfrPct)}
          suffix="%"
          decimals={2}
          status={statusTone(metricStatus(run, 'year5_nsfr_pct'))}
          footer="BoG minimum 100%"
        />
      </div>

      {/* Balance sheet + net income charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Asset projection — 5 years"
            subtitle="Composition of the asset book over the forecast horizon"
          />
          <CardBody>
            <BalanceSheetProjectionChart data={balanceData} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Net income"
            subtitle="Annual profit after tax, GHS millions"
          />
          <CardBody>
            <ResponsiveContainer width="100%" height={340}>
              <BarChart
                data={netIncomeData}
                margin={{ top: 12, right: 16, left: 0, bottom: 8 }}
              >
                <CartesianGrid
                  stroke="#E4E8EC"
                  strokeDasharray="3 3"
                  vertical={false}
                />
                <XAxis
                  dataKey="month"
                  axisLine={{ stroke: '#D0D7DE' }}
                  tickLine={false}
                />
                <YAxis
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}M`}
                  width={48}
                />
                <Tooltip
                  formatter={(v: number) => [
                    `GHS ${v.toLocaleString()}M`,
                    'Net income',
                  ]}
                />
                <Bar
                  dataKey="netIncome"
                  fill="#0E8A4F"
                  radius={[3, 3, 0, 0]}
                  maxBarSize={40}
                />
              </BarChart>
            </ResponsiveContainer>
          </CardBody>
        </Card>
      </div>

      {/* Regulatory ratio paths */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card>
          <CardHeader title="CAR path" subtitle="BoG minimum 10%" />
          <CardBody>
            <RatioHistoryChart
              data={path.map((p) => ({
                month: yearLabel(p),
                value: num(p.carPct),
              }))}
              threshold={10}
              color="#0A2540"
              label="CAR"
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="LCR path" subtitle="BoG minimum 100%" />
          <CardBody>
            <RatioHistoryChart
              data={path.map((p) => ({
                month: yearLabel(p),
                value: num(p.lcrPct),
              }))}
              threshold={100}
              color="#0E8A4F"
              label="LCR"
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="NSFR path" subtitle="BoG minimum 100%" />
          <CardBody>
            <RatioHistoryChart
              data={path.map((p) => ({
                month: yearLabel(p),
                value: num(p.nsfrPct),
              }))}
              threshold={100}
              color="#1A4D5C"
              label="NSFR"
            />
          </CardBody>
        </Card>
      </div>

      {/* Full path table */}
      <Card>
        <CardHeader
          title="5-year projection path"
          subtitle="Annual balance-sheet, P&L, and regulatory ratio path"
          action={<RunBadge run={run} />}
        />
        <CardBody className="p-0">
          <DataTable columns={pathColumns} rows={path} density="compact" />
        </CardBody>
      </Card>

      {/* Validations */}
      <Card>
        <CardHeader
          title="Validations"
          subtitle="Projection integrity and regulatory rule evaluation"
        />
        <CardBody className="p-0">
          <ValidationList validations={run.validations} />
        </CardBody>
      </Card>
    </div>
  );
}
