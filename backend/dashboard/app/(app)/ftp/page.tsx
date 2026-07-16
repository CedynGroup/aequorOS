'use client';

import type { FtpCurvePointRead } from '@aequoros/risk-service-api';
import KpiStat from '@/components/ui/KpiStat';
import Sparkline from '@/components/ui/Sparkline';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import ValidationList from '@/components/ui/ValidationList';
import DataTable, { type Column } from '@/components/ui/DataTable';
import FtpModuleFrame, { type FtpFrameContext } from '@/components/ftp/FtpModuleFrame';
import TransferCurveChart from '@/components/ftp/charts/TransferCurveChart';
import TrendChart from '@/components/ftp/charts/TrendChart';
import { num } from '@/lib/api/values';
import { fmtNum, fmtPct } from '@/lib/format';

export default function FtpCurvePage() {
  return (
    <FtpModuleFrame
      crumb="Curve"
      title="Transfer Curve"
      subtitle="Match-funded FTP curve · base market yield plus liquidity premium and funding spread"
    >
      {(ctx) => <CurveBody ctx={ctx} />}
    </FtpModuleFrame>
  );
}

function CurveBody({ ctx }: { ctx: FtpFrameContext }) {
  const { data, metrics: m } = ctx;

  const trend = data.trend;
  const nimSpark = trend.map((p) => num(p.portfolioNimPct));
  const prior = trend.length >= 2 ? trend[trend.length - 2] : undefined;
  const nimDelta = prior
    ? num(m.portfolioNimPct) - num(prior.portfolioNimPct)
    : undefined;

  const marginFloor = num(m.minProductMarginPct);
  const nim = num(m.portfolioNimPct);

  const nimTrend = trend.map((p) => ({
    label: p.label,
    value: num(p.portfolioNimPct),
    stored: p.stored,
  }));

  const curveValidations = data.validations.filter((v) =>
    v.ruleCode.includes('curve')
  );

  const columns: Column<FtpCurvePointRead>[] = [
    { key: 'tenor', header: 'Tenor', render: (r) => r.tenorLabel, width: '14%' },
    {
      key: 'years',
      header: 'Years',
      numeric: true,
      render: (r) => num(r.tenorYears).toFixed(2),
    },
    {
      key: 'base',
      header: 'Base yield',
      numeric: true,
      render: (r) => fmtPct(num(r.baseYieldPct), 2),
    },
    {
      key: 'liq',
      header: 'Liquidity premium',
      numeric: true,
      render: (r) => `${fmtNum(num(r.liquidityPremiumBps))} bp`,
    },
    {
      key: 'fund',
      header: 'Funding spread',
      numeric: true,
      render: (r) => `${fmtNum(num(r.fundingSpreadBps))} bp`,
    },
    {
      key: 'ftp',
      header: 'FTP rate',
      numeric: true,
      render: (r) => (
        <span className="font-medium">{fmtPct(num(r.ftpRatePct), 2)}</span>
      ),
    },
  ];

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <KpiStat
          label="Blended assigned FTP"
          value={fmtPct(num(m.blendedAssignedFtpPct), 2)}
          hint="Balance-weighted across NMD segments"
        />
        <KpiStat
          label="Portfolio NIM (FTP-adjusted)"
          value={fmtPct(nim, 2)}
          delta={nimDelta}
          status={nim >= marginFloor ? 'ok' : 'crit'}
          sparkline={nimSpark.length >= 2 ? <Sparkline data={nimSpark} /> : undefined}
          hint={`Margin floor ${fmtPct(marginFloor, 1)}`}
        />
        <KpiStat
          label="Weighted asset yield"
          value={fmtPct(num(m.weightedAssetYieldPct), 2)}
          hint="Customer rate net of FTP, asset books"
        />
        <KpiStat
          label="Weighted funding credit"
          value={fmtPct(num(m.weightedFundingCreditPct), 2)}
          hint="FTP credit net of customer rate, funding books"
        />
      </div>

      <ChartFrame
        title="Transfer curve composition"
        subtitle="Base market yield with liquidity-premium and funding-spread bands stacked to the assigned FTP rate"
        height={300}
        footer={
          <span>
            FTP rate = base yield + liquidity premium + funding spread at each tenor ·
            premium bands shown in percentage points
          </span>
        }
      >
        <TransferCurveChart curve={data.curve} />
      </ChartFrame>

      <SectionCard
        title="Curve points"
        subtitle="Published transfer prices by standard tenor"
        noPadding
      >
        <DataTable columns={columns} rows={data.curve} density="compact" />
      </SectionCard>

      {curveValidations.length > 0 && (
        <SectionCard
          title="Curve integrity"
          subtitle="Rule evaluation for the transfer curve"
          noPadding
        >
          <ValidationList validations={curveValidations} />
        </SectionCard>
      )}

      {nimTrend.length >= 2 && (
        <ChartFrame
          title="Portfolio NIM trend"
          subtitle="Trailing periods against the margin floor · hollow points are inline computations"
          height={260}
        >
          <TrendChart
            data={nimTrend}
            threshold={marginFloor}
            thresholdLabel={`Floor ${fmtPct(marginFloor, 1)}`}
            valueLabel="Portfolio NIM"
            format={(v) => fmtPct(v, 2)}
          />
        </ChartFrame>
      )}
    </>
  );
}
