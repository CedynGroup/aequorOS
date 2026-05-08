import PageHeader from '@/components/ui/PageHeader';
import KPICard from '@/components/ui/KPICard';
import StatusPill from '@/components/ui/StatusPill';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import GapAnalysisChart from '@/components/charts/GapAnalysisChart';
import RatioHistoryChart from '@/components/charts/RatioHistoryChart';
import { gapBuckets, irrKpis, niiHistory, type GapBucket } from '@/lib/data/irr';
import { bank } from '@/lib/data/bank';
import { fmtCurrency } from '@/lib/format';

const gapColumns: Column<GapBucket>[] = [
  { key: 'tenor', header: 'Tenor', render: (r) => r.tenor },
  { key: 'rsa', header: 'Rate-sensitive assets', numeric: true, render: (r) => fmtCurrency(r.rsa) },
  { key: 'rsl', header: 'Rate-sensitive liabilities', numeric: true, render: (r) => fmtCurrency(r.rsl) },
  {
    key: 'gap',
    header: 'Period gap',
    numeric: true,
    render: (r) => (
      <span className={r.gap >= 0 ? 'text-success' : 'text-warning'}>
        {r.gap >= 0 ? '+' : ''}
        {fmtCurrency(Math.abs(r.gap))}
      </span>
    ),
  },
  {
    key: 'cum',
    header: 'Cumulative gap',
    numeric: true,
    render: (r) => (
      <span className={r.cumGap >= 0 ? 'text-success' : 'text-warning'}>
        {r.cumGap >= 0 ? '+' : ''}
        {fmtCurrency(Math.abs(r.cumGap))}
      </span>
    ),
  },
];

export default function IRRDashboard() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Interest Rate Risk' },
          { label: 'Dashboard' },
        ]}
        title="Interest Rate Risk"
        subtitle="Banking book IRRBB · BoG CRD tenor framework · Gap, NII, EVE"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <KPICard
            label="NII at risk (12M, +200bps)"
            value={irrKpis.niiAtRisk1Y / 1_000_000}
            prefix="GHS"
            suffix="M"
            decimals={1}
            footer={`${irrKpis.niiAtRisk1YPct.toFixed(1)}% of base NII`}
            status="compliant"
            sparkline={[16, 17, 18, 17, 18, 18, 19, 18.4]}
          />
          <KPICard
            label="EVE sensitivity (+200bps)"
            value={irrKpis.eveSensitivity200 / 1_000_000}
            prefix="GHS"
            suffix="M"
            decimals={1}
            footer={`${Math.abs(irrKpis.eveSensitivity200Pct).toFixed(1)}% of Tier 1 (limit ${irrKpis.eveLimitPct}%)`}
            status="approaching"
            sparkline={[24, 24, 25, 26, 26, 27, 27, 27.3]}
          />
          <KPICard
            label="Asset duration"
            value={irrKpis.duration}
            suffix=" yrs"
            decimals={2}
            footer={`Duration gap ${irrKpis.durationGap.toFixed(2)} yrs`}
            status="compliant"
          />
          <KPICard
            label="Open IRS notional"
            value={211}
            prefix="GHS"
            suffix="M"
            decimals={0}
            footer="5 active contracts · 4 GHS / 1 USD"
            status="compliant"
          />
        </div>

        <Card>
          <CardHeader
            title="Repricing gap by tenor bucket"
            subtitle="Bank of Ghana CRD framework · Banking book exposure"
            action={<StatusPill tone="compliant">Within bucket limits</StatusPill>}
          />
          <CardBody>
            <GapAnalysisChart data={gapBuckets} />
          </CardBody>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card className="lg:col-span-2">
            <CardHeader
              title="Gap analysis detail"
              subtitle="Rate-sensitive assets and liabilities by repricing tenor"
            />
            <CardBody className="p-0">
              <DataTable
                columns={gapColumns}
                rows={[
                  ...gapBuckets,
                  {
                    tenor: 'TOTAL',
                    rsa: gapBuckets.reduce((s, b) => s + b.rsa, 0),
                    rsl: gapBuckets.reduce((s, b) => s + b.rsl, 0),
                    gap: gapBuckets[gapBuckets.length - 1].cumGap,
                    cumGap: gapBuckets[gapBuckets.length - 1].cumGap,
                  } as GapBucket,
                ]}
                totalsRowMatcher={(r) => r.tenor === 'TOTAL'}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Net Interest Income — 12M trend"
              subtitle="Actual vs projected, GHS millions"
            />
            <CardBody>
              <RatioHistoryChart
                data={niiHistory.map((p) => ({ month: p.month, value: p.actual }))}
                threshold={20}
                yMin={20}
                yMax={28}
                color="#2D7FF9"
                label="NII"
              />
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}
