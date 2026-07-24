'use client';

import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import type { FtpProductRead } from '@aequoros/risk-service-api';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import FtpModuleFrame, { type FtpFrameContext } from '@/components/ftp/FtpModuleFrame';
import MarginBars, { type MarginBarPoint } from '@/components/ftp/charts/MarginBars';
import { labelize, num } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

type SortKey = 'balance' | 'margin' | 'contribution' | 'customer' | 'ftp';
type SortDir = 'asc' | 'desc';

const SORT_ACCESSORS: Record<SortKey, (r: FtpProductRead) => number> = {
  balance: (r) => num(r.balanceGhs),
  margin: (r) => num(r.netMarginPct),
  contribution: (r) => num(r.contributionGhs),
  customer: (r) => num(r.customerRatePct),
  ftp: (r) => num(r.ftpRatePct),
};

export default function FtpProductsPage() {
  return (
    <FtpModuleFrame
      crumb="Product Profitability"
      title="Product Profitability"
      subtitle="Match-funded net margin by product · opex, ECL, and capital charges deducted"
    >
      {(ctx) => <ProductsBody ctx={ctx} />}
    </FtpModuleFrame>
  );
}

function ProductsBody({ ctx }: { ctx: FtpFrameContext }) {
  const { data, metrics: m } = ctx;
  const [sortKey, setSortKey] = useState<SortKey>('balance');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const marginFloor = num(m.minProductMarginPct);

  const sorted = useMemo(() => {
    const accessor = SORT_ACCESSORS[sortKey];
    const factor = sortDir === 'asc' ? 1 : -1;
    return [...data.products].sort((a, b) => factor * (accessor(a) - accessor(b)));
  }, [data.products, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const sortHeader = (label: string, key: SortKey) => (
    <button
      type="button"
      onClick={() => toggleSort(key)}
      className="inline-flex items-center gap-1 uppercase tracking-wider font-medium hover:text-navy"
    >
      {label}
      {sortKey === key ? (
        sortDir === 'asc' ? (
          <ArrowUp size={11} aria-hidden />
        ) : (
          <ArrowDown size={11} aria-hidden />
        )
      ) : (
        <ArrowUpDown size={11} className="opacity-40" aria-hidden />
      )}
    </button>
  );

  const marginBars: MarginBarPoint[] = [...data.products]
    .sort((a, b) => num(b.netMarginPct) - num(a.netMarginPct))
    .map((p) => ({
      label: labelize(p.product),
      value: num(p.netMarginPct),
      side: p.category,
      flagged: p.belowMinMargin,
    }));

  const columns: Column<FtpProductRead>[] = [
    {
      key: 'product',
      header: 'Product',
      render: (r) => (
        <span className={r.belowMinMargin ? 'text-critical font-medium' : undefined}>
          {labelize(r.product)}
        </span>
      ),
      width: '18%',
    },
    {
      key: 'category',
      header: 'Book',
      render: (r) => (
        <StatusPill tone={r.category === 'asset' ? 'action' : 'slate'}>
          {labelize(r.category)}
        </StatusPill>
      ),
    },
    {
      key: 'balance',
      header: sortHeader('Balance', 'balance'),
      numeric: true,
      render: (r) => fmtCurrency(num(r.balanceGhs)),
    },
    {
      key: 'customer',
      header: sortHeader('Customer rate', 'customer'),
      numeric: true,
      render: (r) => fmtPct(num(r.customerRatePct), 2),
    },
    {
      key: 'ftp',
      header: sortHeader('FTP rate', 'ftp'),
      numeric: true,
      render: (r) => fmtPct(num(r.ftpRatePct), 2),
    },
    {
      key: 'opex',
      header: 'Opex',
      numeric: true,
      render: (r) => fmtPct(num(r.operatingCostPct), 2),
    },
    {
      key: 'ecl',
      header: 'ECL',
      numeric: true,
      render: (r) => fmtPct(num(r.expectedCreditLossPct), 2),
    },
    {
      key: 'capital',
      header: 'Capital',
      numeric: true,
      render: (r) => fmtPct(num(r.capitalChargePct), 2),
    },
    {
      key: 'margin',
      header: sortHeader('Net margin', 'margin'),
      numeric: true,
      render: (r) => (
        <span className={r.belowMinMargin ? 'text-critical font-medium' : 'font-medium'}>
          {fmtPct(num(r.netMarginPct), 2)}
        </span>
      ),
    },
    {
      key: 'contribution',
      header: sortHeader('Contribution', 'contribution'),
      numeric: true,
      render: (r) => fmtCurrencySigned(num(r.contributionGhs)),
    },
  ];

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <KpiStat
          label="Portfolio NIM (balance-weighted)"
          value={fmtPct(num(m.portfolioNimPct), 2)}
          status={num(m.portfolioNimPct) >= marginFloor ? 'ok' : 'crit'}
          hint={`Margin floor ${fmtPct(marginFloor, 1)}`}
        />
        <KpiStat
          label="Total net contribution"
          value={fmtCurrency(num(m.totalContributionGhs))}
          hint={`Across ${fmtCurrency(num(m.totalBalanceGhs))} of balances`}
        />
        <KpiStat
          label="Products below floor"
          value={`${m.productsBelowMinMargin} of ${m.totalProducts}`}
          status={m.productsBelowMinMargin > 0 ? 'warn' : 'ok'}
          hint={
            m.productsBelowMinMargin > 0
              ? 'Match-funded margin below the floor'
              : 'All products clear the floor'
          }
        />
        <KpiStat
          label="Weighted asset yield / funding credit"
          value={`${fmtPct(num(m.weightedAssetYieldPct), 2)}`}
          hint={`Funding credit ${fmtPct(num(m.weightedFundingCreditPct), 2)}`}
        />
      </div>

      <ChartFrame
        title="Net margin by product"
        subtitle="Match-funded margin after opex, ECL, and capital charge · asset vs funding books"
        height={Math.max(260, marginBars.length * 30 + 60)}
        footer={
          <span>
            Blue bars are asset books, teal bars funding books; red bars sit below the{' '}
            {fmtPct(marginFloor, 1)} floor
          </span>
        }
      >
        <MarginBars
          data={marginBars}
          mode="pct"
          floorPct={marginFloor}
          height={Math.max(260, marginBars.length * 30 + 40)}
        />
      </ChartFrame>

      <SectionCard
        title="Product margin detail"
        subtitle="Click a column header to sort · below-floor products flagged in red"
        noPadding
        actions={
          <StatusPill tone={m.productsBelowMinMargin > 0 ? 'amber' : 'success'}>
            {m.productsBelowMinMargin} below floor
          </StatusPill>
        }
      >
        <DataTable
          columns={columns}
          rows={sorted}
          density="compact"
          totalsRowMatcher={(r) => r.belowMinMargin}
        />
      </SectionCard>
    </>
  );
}
