'use client';

import { useMemo, useState } from 'react';
import { Info } from 'lucide-react';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import FtpModuleFrame, { type FtpFrameContext } from '@/components/ftp/FtpModuleFrame';
import MarginBars, { type MarginBarPoint } from '@/components/ftp/charts/MarginBars';
import {
  GROUPING_RULE,
  aggregateBusinessLines,
  type BusinessLine,
} from '@/components/ftp/businessLines';
import { labelize, num } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

export default function FtpLinesPage() {
  return (
    <FtpModuleFrame
      crumb="Business Lines"
      title="Business Line P&L"
      subtitle="FTP-adjusted contribution rolled up from product books into desk-level lines"
    >
      {(ctx) => <LinesBody ctx={ctx} />}
    </FtpModuleFrame>
  );
}

function LinesBody({ ctx }: { ctx: FtpFrameContext }) {
  const { data, metrics: m } = ctx;
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const lines = useMemo(() => aggregateBusinessLines(data.products), [data.products]);
  const selected = selectedKey ? lines.find((l) => l.key === selectedKey) : undefined;

  const assetLines = lines.filter((l) => l.side === 'asset');
  const liabilityLines = lines.filter((l) => l.side === 'liability');
  const topLine =
    lines.length > 0
      ? lines.reduce((best, l) => (l.contributionGhs > best.contributionGhs ? l : best))
      : undefined;

  const bars: MarginBarPoint[] = [...lines]
    .sort((a, b) => b.contributionGhs - a.contributionGhs)
    .map((l) => ({
      label: l.label,
      value: l.contributionGhs,
      side: l.side,
      flagged: l.belowFloorCount > 0,
    }));

  const columns: Column<BusinessLine>[] = [
    { key: 'line', header: 'Business line', render: (r) => r.label, width: '26%' },
    {
      key: 'side',
      header: 'Side',
      render: (r) => (
        <StatusPill tone={r.side === 'asset' ? 'action' : r.side === 'liability' ? 'slate' : 'amber'}>
          {r.side === 'mixed' ? 'Mixed' : labelize(r.side)}
        </StatusPill>
      ),
    },
    {
      key: 'products',
      header: 'Products',
      numeric: true,
      render: (r) => String(r.products.length),
    },
    {
      key: 'balance',
      header: 'Balance',
      numeric: true,
      render: (r) => fmtCurrency(r.balanceGhs, 'GHS'),
    },
    {
      key: 'margin',
      header: 'Weighted margin',
      numeric: true,
      render: (r) => fmtPct(r.weightedMarginPct, 2),
    },
    {
      key: 'contribution',
      header: 'Net contribution',
      numeric: true,
      render: (r) => fmtCurrencySigned(r.contributionGhs, 'GHS'),
    },
    {
      key: 'floor',
      header: 'Below floor',
      align: 'right',
      render: (r) =>
        r.belowFloorCount > 0 ? (
          <StatusPill tone="amber">{r.belowFloorCount}</StatusPill>
        ) : (
          <span className="text-slate">—</span>
        ),
    },
  ];

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <KpiStat
          label="Total net contribution"
          value={fmtCurrency(num(m.totalContributionGhs), 'GHS')}
          hint="All product books, FTP-adjusted"
        />
        <KpiStat
          label="Business lines"
          value={String(lines.length)}
          hint={`${assetLines.length} asset · ${liabilityLines.length} funding`}
        />
        <KpiStat
          label="Top contributor"
          value={topLine ? fmtCurrency(topLine.contributionGhs, 'GHS') : '—'}
          hint={topLine?.label ?? 'No products'}
        />
        <KpiStat
          label="Portfolio NIM"
          value={fmtPct(num(m.portfolioNimPct), 2)}
          hint="Balance-weighted across all books"
        />
      </div>

      <div className="card px-5 py-3.5 flex items-start gap-3 border-l-4 border-l-action">
        <Info size={16} className="text-action shrink-0 mt-0.5" aria-hidden />
        <p className="text-caption text-slate leading-relaxed">{GROUPING_RULE}</p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <ChartFrame
          title="Net contribution by line"
          subtitle="Σ product contribution per line · asset vs funding books"
          height={Math.max(240, bars.length * 36 + 60)}
        >
          <MarginBars
            data={bars}
            mode="ghs"
            height={Math.max(240, bars.length * 36 + 40)}
          />
        </ChartFrame>

        <SectionCard
          title="Line P&L"
          subtitle="Weighted margin = Σ contribution ÷ Σ balance per line"
          noPadding
          footer={<span>Select a line to see its member products.</span>}
        >
          <DataTable
            columns={columns}
            rows={lines}
            density="compact"
            onRowClick={(r) =>
              setSelectedKey((current) => (current === r.key ? null : r.key))
            }
            rowClassName={(r) => (r.key === selectedKey ? 'bg-action-light/40' : '')}
          />
        </SectionCard>
      </div>

      {selected && (
        <SectionCard
          title={`${selected.label} — member products`}
          subtitle="Backend product rows contributing to this line"
          noPadding
          actions={
            <button
              type="button"
              onClick={() => setSelectedKey(null)}
              className="text-caption font-medium text-slate hover:text-navy"
            >
              Close
            </button>
          }
        >
          <DataTable
            columns={[
              {
                key: 'product',
                header: 'Product',
                render: (r) => labelize(r.product),
                width: '28%',
              },
              {
                key: 'balance',
                header: 'Balance',
                numeric: true,
                render: (r) => fmtCurrency(num(r.balanceGhs), 'GHS'),
              },
              {
                key: 'margin',
                header: 'Net margin',
                numeric: true,
                render: (r) => fmtPct(num(r.netMarginPct), 2),
              },
              {
                key: 'contribution',
                header: 'Contribution',
                numeric: true,
                render: (r) => fmtCurrencySigned(num(r.contributionGhs), 'GHS'),
              },
              {
                key: 'floor',
                header: 'Floor',
                align: 'right',
                render: (r) => (
                  <StatusPill tone={r.belowMinMargin ? 'breach' : 'compliant'}>
                    {r.belowMinMargin ? 'Below' : 'Clear'}
                  </StatusPill>
                ),
              },
            ]}
            rows={selected.products}
            density="compact"
          />
        </SectionCard>
      )}
    </>
  );
}
