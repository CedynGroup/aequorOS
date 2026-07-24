'use client';

import { useMemo } from 'react';
import type { ReactNode } from 'react';
import type { FtpNmdSegmentRead } from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import ValidationList from '@/components/ui/ValidationList';
import DataTable, { type Column } from '@/components/ui/DataTable';
import FtpModuleFrame, { type FtpFrameContext } from '@/components/ftp/FtpModuleFrame';
import { ftpRunParameters } from '@/components/ftp/params';
import { labelize, num, statusTone } from '@/lib/api/values';
import { fmtCurrency, fmtNum, fmtPct } from '@/lib/format';

export default function FtpRulesPage() {
  return (
    <FtpModuleFrame
      crumb="Rules"
      title="FTP Methodology & Rules"
      subtitle="The parameter set the pricing engine ran with — nothing on this page is hard-coded in the UI"
    >
      {(ctx) => <RulesBody ctx={ctx} />}
    </FtpModuleFrame>
  );
}

function RulesBody({ ctx }: { ctx: FtpFrameContext }) {
  const { data, metrics: m, run } = ctx;
  const params = useMemo(() => ftpRunParameters(run), [run]);

  const marginFloor = num(m.minProductMarginPct);
  const nmdMin = num(m.nmdCoreMinPct);
  const nmdMax = num(m.nmdCoreMaxPct);

  const premiumValidation = data.validations.find(
    (v) => v.ruleCode === 'curve_within_premium_limits'
  );

  const snapshotHint = params
    ? 'From the stored run parameter snapshot'
    : 'Run all scenarios to persist the parameter snapshot';

  return (
    <>
      <SectionCard
        title="Pricing methodology"
        subtitle="How each figure in this module is produced"
      >
        <ul className="space-y-2 text-body text-slate leading-relaxed list-disc pl-5">
          <li>
            Every product is match-funded against the transfer curve at its
            contractual tenor: asset books pay the FTP rate, funding books earn
            it as a credit.
          </li>
          <li>
            Product net margin deducts operating cost, expected credit loss,
            and the capital charge from the customer-rate-vs-FTP spread; a
            product breaching the margin floor is flagged on every view.
          </li>
          <li>
            Non-maturing deposits are behaviouralised into core and volatile
            tranches; each tranche is priced at its own point on the curve and
            the blended rate must keep the core share inside the policy band.
          </li>
          <li>
            The stress overlays reprice the whole book on a shifted curve
            (rates-up) or with an additional funding spread (funding stress) —
            results are on the Ex-ante vs Ex-post tab.
          </li>
        </ul>
      </SectionCard>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <RuleCard
          title="Product margin floor"
          value={fmtPct(marginFloor, 2)}
          source="From the dashboard metrics"
          detail={`${m.productsBelowMinMargin} of ${m.totalProducts} products currently below the floor`}
          pill={
            <StatusPill tone={m.productsBelowMinMargin > 0 ? 'amber' : 'success'}>
              {m.productsBelowMinMargin > 0 ? 'Attention' : 'Clear'}
            </StatusPill>
          }
        />
        <RuleCard
          title="NMD core policy band"
          value={`${fmtPct(nmdMin, 0)} – ${fmtPct(nmdMax, 0)}`}
          source="From the dashboard metrics"
          detail={`Measured core share ${fmtPct(num(m.nmdCorePct), 1)}`}
          pill={<StatusPill tone={statusTone(m.nmdCoreStatus)} />}
        />
        <RuleCard
          title="Target return on equity"
          value={params?.targetRoePct !== null && params ? fmtPct(params.targetRoePct, 1) : '—'}
          source={snapshotHint}
          detail="Drives the capital charge deducted from product margins"
        />
        <RuleCard
          title="Liquidity premium cap"
          value={
            params?.liquidityPremiumMaxBps !== null && params
              ? `${fmtNum(params.liquidityPremiumMaxBps)} bp`
              : '—'
          }
          source={snapshotHint}
          detail="Maximum liquidity premium a curve point may carry"
        />
        <RuleCard
          title="Funding spread cap"
          value={
            params?.fundingSpreadMaxBps !== null && params
              ? `${fmtNum(params.fundingSpreadMaxBps)} bp`
              : '—'
          }
          source={snapshotHint}
          detail="Maximum funding spread a curve point may carry"
        />
        <RuleCard
          title="Stress overlays"
          value={
            params && params.ratesUpShiftBps !== null && params.fundingStressAddBps !== null
              ? `+${fmtNum(params.ratesUpShiftBps)} bp / +${fmtNum(
                  params.fundingStressAddBps
                )} bp`
              : '—'
          }
          source={snapshotHint}
          detail="Parallel curve shift (rates-up) · additive funding spread (funding stress)"
        />
      </div>

      {premiumValidation && (
        <SectionCard
          title="Curve cap evaluation"
          subtitle="The engine's own verdict on the premium and spread caps"
          noPadding
        >
          <ValidationList validations={[premiumValidation]} />
        </SectionCard>
      )}

      <SectionCard
        title="NMD behaviouralisation"
        subtitle="Core / volatile split, effective duration, and assigned FTP per segment"
        noPadding
      >
        <NmdTable rows={data.nmdSegments} />
      </SectionCard>

      <SectionCard
        title="All validations"
        subtitle="FTP curve, margin, and NMD policy rule evaluation for this period"
        noPadding
      >
        <ValidationList validations={data.validations} />
      </SectionCard>
    </>
  );
}

function RuleCard({
  title,
  value,
  source,
  detail,
  pill,
}: {
  title: string;
  value: string;
  source: string;
  detail?: string;
  pill?: ReactNode;
}) {
  return (
    <div className="card p-5 flex flex-col gap-2 min-w-0">
      <div className="flex items-start justify-between gap-2">
        <p className="text-caption font-medium text-slate uppercase tracking-wider">
          {title}
        </p>
        {pill}
      </div>
      <p className="font-mono text-kpi text-navy tnum">{value}</p>
      {detail && <p className="text-caption text-slate leading-snug">{detail}</p>}
      <p className="mt-auto text-micro text-slate-light uppercase tracking-wider">
        {source}
      </p>
    </div>
  );
}

function NmdTable({ rows }: { rows: FtpNmdSegmentRead[] }) {
  const columns: Column<FtpNmdSegmentRead>[] = [
    {
      key: 'segment',
      header: 'Segment',
      render: (r) => labelize(r.segment),
      width: '18%',
    },
    {
      key: 'balance',
      header: 'Balance',
      numeric: true,
      render: (r) => fmtCurrency(num(r.balanceGhs)),
    },
    {
      key: 'core',
      header: 'Core',
      numeric: true,
      render: (r) => fmtPct(num(r.corePct), 1),
    },
    {
      key: 'volatile',
      header: 'Volatile',
      numeric: true,
      render: (r) => fmtPct(num(r.volatilePct), 1),
    },
    {
      key: 'duration',
      header: 'Eff. duration',
      numeric: true,
      render: (r) => `${num(r.effectiveDurationYears).toFixed(2)}y`,
    },
    {
      key: 'coreFtp',
      header: 'Core FTP',
      numeric: true,
      render: (r) => fmtPct(num(r.coreFtpPct), 2),
    },
    {
      key: 'volFtp',
      header: 'Volatile FTP',
      numeric: true,
      render: (r) => fmtPct(num(r.volatileFtpPct), 2),
    },
    {
      key: 'assigned',
      header: 'Assigned FTP',
      numeric: true,
      render: (r) => (
        <span className="font-medium">{fmtPct(num(r.assignedFtpPct), 2)}</span>
      ),
    },
    {
      key: 'policy',
      header: 'Policy',
      align: 'right',
      render: (r) => (
        <StatusPill tone={r.withinPolicy ? 'compliant' : 'breach'}>
          {r.withinPolicy ? 'Within' : 'Breach'}
        </StatusPill>
      ),
    },
  ];
  return <DataTable columns={columns} rows={rows} density="compact" />;
}
