'use client';

/**
 * Standing credit-concentration monitor: portfolio concentration by
 * counterparty, sector, geography, product, collateral and employer, against
 * Board limits (BoG concentration guidelines, September 2025).
 *
 * Employer is first-class because payroll / check-off lending concentrates
 * repayment risk on the employer's remittance, not the borrower. Coverage is
 * disclosed per dimension; loans that do not state a dimension are excluded,
 * never grouped as "Unknown". A limit that is not configured renders "Not
 * set" — no invented threshold, no fabricated compliance colour.
 */

import { useState } from 'react';
import Link from 'next/link';
import type { Column } from '@/components/ui/DataTable';
import type { ConcentrationBucketRead } from '@aequoros/risk-service-api';
import CreditWorkspace from '@/components/credit/CreditWorkspace';
import DataTable from '@/components/ui/DataTable';
import KpiStat from '@/components/ui/KpiStat';
import QueryBoundary from '@/components/ui/QueryBoundary';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import SubTabs from '@/components/ui/SubTabs';
import EmptyState from '@/components/ui/EmptyState';
import { Users } from 'lucide-react';
import { useBankContext } from '@/components/shell/BankContext';
import { useCreditConcentration } from '@/lib/api/hooks';
import { labelize, num } from '@/lib/api/values';
import { fmtCurrency, fmtInt, regShort } from '@/lib/format';

const DIMENSIONS = [
  { key: 'single_name', label: 'Single name' },
  { key: 'sector', label: 'Sector' },
  { key: 'geography', label: 'Geography' },
  { key: 'product', label: 'Product' },
  { key: 'collateral', label: 'Collateral' },
  { key: 'employer', label: 'Employer' },
];

function statusPill(status: string) {
  if (status === 'above_limit') return <StatusPill tone="critical">Above limit</StatusPill>;
  if (status === 'within_limit') return <StatusPill tone="success">Within limit</StatusPill>;
  if (status === 'not_computable') return <StatusPill tone="slate">Not computable</StatusPill>;
  return <StatusPill tone="slate">Not set</StatusPill>;
}

export default function CreditConcentrationPage() {
  const [dimension, setDimension] = useState('single_name');
  const { bank } = useBankContext();
  const concentration = useCreditConcentration(bank?.id);

  return (
    <CreditWorkspace
      crumb="Concentration"
      subtitle="Portfolio concentration by counterparty, sector, geography, product, collateral and employer, against Board limits."
    >
      {() => (
        <QueryBoundary
          isLoading={concentration.isLoading}
          error={concentration.error}
          onRetry={() => concentration.refetch()}
        >
          {concentration.data
            ? (() => {
                const data = concentration.data;
                const active = data.dimensions.find((d) => d.dimension === dimension);
                const capitalLabel =
                  data.capitalBasis === 'net_own_funds'
                    ? 'of Net Own Funds'
                    : 'of Tier 1 capital';
                const columns: Column<ConcentrationBucketRead>[] = [
                  {
                    key: 'name',
                    header:
                      dimension === 'single_name'
                        ? 'Counterparty / group'
                        : DIMENSIONS.find((d) => d.key === dimension)?.label ?? 'Bucket',
                    render: (r) => (
                      <span className="text-caption text-navy">
                        {r.key.startsWith('cp:') || r.key.startsWith('group:') || r.key.startsWith('pos:')
                          ? r.key.replace(/^(cp|group|pos):/, '')
                          : labelize(r.key)}
                      </span>
                    ),
                  },
                  {
                    key: 'loans',
                    header: 'Exposures',
                    align: 'right',
                    numeric: true,
                    render: (r) => fmtInt(r.loanCount),
                  },
                  {
                    key: 'exposure',
                    header: 'Exposure',
                    align: 'right',
                    numeric: true,
                    render: (r) => fmtCurrency(num(r.exposureGhs)),
                  },
                  {
                    key: 'book',
                    header: '% of book',
                    align: 'right',
                    numeric: true,
                    render: (r) => `${num(r.shareOfBookPct).toFixed(2)}%`,
                  },
                  {
                    key: 'capital',
                    header: `% ${capitalLabel}`,
                    align: 'right',
                    numeric: true,
                    render: (r) =>
                      r.shareOfCapitalPct != null
                        ? `${num(r.shareOfCapitalPct).toFixed(2)}%`
                        : '—',
                  },
                  {
                    key: 'limit',
                    header: 'Board limit',
                    align: 'right',
                    numeric: true,
                    render: (r) =>
                      r.limitValue != null ? `${num(r.limitValue).toFixed(1)}%` : 'Not set',
                  },
                  {
                    key: 'util',
                    header: 'Utilisation',
                    align: 'right',
                    numeric: true,
                    render: (r) =>
                      r.utilizationPct != null ? `${num(r.utilizationPct).toFixed(0)}%` : '—',
                  },
                  {
                    key: 'status',
                    header: 'Status',
                    align: 'right',
                    render: (r) => statusPill(r.limitStatus),
                  },
                ];
                return (
                  <>
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                      <KpiStat
                        label="Credit exposure book"
                        value={fmtCurrency(num(data.totalBookGhs))}
                        hint="Loans, placements and securities"
                      />
                      <KpiStat
                        label="Capital base"
                        value={
                          data.capitalBaseGhs != null
                            ? fmtCurrency(num(data.capitalBaseGhs))
                            : 'Not computable'
                        }
                        hint={
                          data.capitalBasis === 'net_own_funds'
                            ? 'Act 930 s.29 Net Own Funds'
                            : 'Tier 1 from current capital components'
                        }
                      />
                      <KpiStat
                        label="Board limits configured"
                        value={fmtInt(data.limitCount)}
                        hint={
                          data.limitCount === 0
                            ? 'The register is empty — limits are a Board decision'
                            : 'Active limit rows'
                        }
                      />
                      <KpiStat
                        label="Limits breached"
                        value={fmtInt(data.breaches.length)}
                        status={data.breaches.length > 0 ? 'crit' : 'ok'}
                      />
                    </div>

                    <SubTabs items={DIMENSIONS} active={dimension} onChange={setDimension} />

                    {active && active.bucketCount === 0 && dimension === 'employer' ? (
                      <EmptyState
                        Icon={Users}
                        title="No employer data on this book"
                        description="Employer concentration needs the employer attribute on ingested loans — add an employer column to the loan file or map it in the Data Engine. Loans without it are excluded, never grouped as Unknown."
                        action={
                          <Link href="/data-engine" className="btn-primary">
                            Open the Data Engine
                          </Link>
                        }
                      />
                    ) : active ? (
                      <SectionCard
                        title={`Top ${DIMENSIONS.find((d) => d.key === dimension)?.label.toLowerCase()} concentrations`}
                        subtitle={`HHI ${fmtInt(num(active.hhi))} across ${fmtInt(active.bucketCount)} bucket(s) · basis 0–10,000`}
                        noPadding
                        footer={
                          num(active.coveragePct) < 100
                            ? `Computed over the ${num(active.coveragePct).toFixed(1)}% of the book that states this dimension; the remainder is excluded, not grouped.`
                            : undefined
                        }
                      >
                        <DataTable columns={columns} rows={active.buckets} density="compact" />
                      </SectionCard>
                    ) : null}

                    <SectionCard title="About this monitor">
                      <p className="text-body text-navy/85 leading-relaxed">
                        The {regShort()} guidelines on credit concentration risk require the Board
                        to approve a limit structure per concentration dimension, with breach
                        escalation, by end-2026. The guidelines prescribe no numeric limits, so
                        this register starts empty and every limit shown is a Board decision with
                        approval evidence. Single-name concentration is measured at the obligor
                        and connected-group level, never per facility.
                      </p>
                    </SectionCard>
                  </>
                );
              })()
            : null}
        </QueryBoundary>
      )}
    </CreditWorkspace>
  );
}
