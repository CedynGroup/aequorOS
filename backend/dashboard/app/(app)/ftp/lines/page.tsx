'use client';

import { useMemo, useState } from 'react';
import { Info } from 'lucide-react';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import FtpModuleFrame, { type FtpFrameContext } from '@/components/ftp/FtpModuleFrame';
import IllustrativeBadge from '@/components/ftp/IllustrativeBadge';
import MarginBars, { type MarginBarPoint } from '@/components/ftp/charts/MarginBars';
import {
  GROUPING_RULE,
  MARGIN_NOTICE,
  aggregateBusinessLines,
  type BusinessLine,
} from '@/components/ftp/businessLines';
import { fmtPctOrNull, labelize, num } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

/** Tooltip carried by every marker that flags this page's grouped figures. */
const VIEW_BADGE_TITLE =
  'Grouped and divided in this screen. The FTP engine prices products and reports branches — it publishes no business-line margin, so no engine figure sits behind this column.';

export default function FtpLinesPage() {
  return (
    <FtpModuleFrame
      crumb="Business Lines"
      title="Business Line P&L"
      subtitle="FTP-adjusted product contribution grouped into desk-level lines for comparison — a screen view, not an engine output"
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
      render: (r) => fmtCurrency(r.balanceGhs),
    },
    {
      key: 'margin',
      header: 'Implied margin (view)',
      numeric: true,
      // NOT COMPUTABLE is a first-class outcome: a line with no balance has no
      // margin, and a rendered 0% would read as a real — and unusually good —
      // one. Same defect class as the `?? '0'` floors removed from the
      // regulatory screens.
      render: (r) =>
        r.impliedMarginPct === null ? (
          <span className="text-slate" title="This line carries no balance, so no margin can be worked out.">
            Not computable
          </span>
        ) : (
          fmtPctOrNull(r.impliedMarginPct, 2)
        ),
    },
    {
      key: 'contribution',
      header: 'Net contribution',
      numeric: true,
      render: (r) => fmtCurrencySigned(r.contributionGhs),
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
          value={fmtCurrency(num(m.totalContributionGhs))}
          hint="All product books, FTP-adjusted"
        />
        <KpiStat
          label="Business lines"
          value={String(lines.length)}
          hint={`${assetLines.length} asset · ${liabilityLines.length} funding`}
        />
        <KpiStat
          label="Top contributor"
          value={topLine ? fmtCurrency(topLine.contributionGhs) : '—'}
          hint={topLine?.label ?? 'No products'}
        />
        <KpiStat
          label="Portfolio NIM"
          value={fmtPct(num(m.portfolioNimPct), 2)}
          hint="Balance-weighted across all books"
        />
      </div>

      <div className="card px-5 py-3.5 flex items-start gap-3 border-l-4 border-l-warning">
        <Info size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
        <div className="space-y-1.5">
          <p className="text-caption font-medium text-navy">
            How to read this page{' '}
            <IllustrativeBadge label="View aggregate" title={VIEW_BADGE_TITLE} className="ml-1 align-middle" />
          </p>
          <p className="text-caption text-slate leading-relaxed">{GROUPING_RULE}</p>
          <p className="text-caption text-slate leading-relaxed">{MARGIN_NOTICE}</p>
        </div>
      </div>

      <ChartFrame
        title="Net contribution by line"
        subtitle="Engine product contribution, summed per group · asset vs funding books"
        height={Math.max(240, bars.length * 36 + 60)}
      >
        <MarginBars
          data={bars}
          mode="ghs"
          height={Math.max(240, bars.length * 36 + 40)}
        />
      </ChartFrame>

      {/*
        The table takes the whole row, below the chart, rather than sharing an
        `xl:grid-cols-2` row with it. Seven columns needed 769px and the
        half-width card gave them 466px at 1280 and 666px at 1680, so "Implied
        margin (view)", "Net contribution" and "Below floor" — the three
        columns this page exists to show, and the one the chart beside it plots
        — were off-screen at every realistic viewport. `DataTable` now signposts
        an overflow wherever one remains; here the honest fix is to remove the
        overflow, because the cause was the layout, not the missing signpost.
      */}
      <SectionCard
        title="Line P&L"
        subtitle="Balances and contributions are engine figures, summed per group; the implied margin is this view's own division of the two."
        noPadding
        actions={<IllustrativeBadge label="View aggregate" title={VIEW_BADGE_TITLE} />}
        footer={<span>Select a line to see its member products.</span>}
      >
        <DataTable
          columns={columns}
          rows={lines}
          density="compact"
          scrollLabel="Line P&L"
          onRowClick={(r) =>
            setSelectedKey((current) => (current === r.key ? null : r.key))
          }
          rowClassName={(r) => (r.key === selectedKey ? 'bg-action-light/40' : '')}
        />
      </SectionCard>

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
                render: (r) => fmtCurrency(num(r.balanceGhs)),
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
                render: (r) => fmtCurrencySigned(num(r.contributionGhs)),
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
