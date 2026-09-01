'use client';

/**
 * Delinquency & Migration: days-past-due distribution, portfolio at risk, and
 * the Notice-mandated month-over-month state migration with DPD roll rates.
 */

import type { Column } from '@/components/ui/DataTable';
import type { PortfolioAtRiskRead } from '@aequoros/risk-service-api';
import CreditWorkspace from '@/components/credit/CreditWorkspace';
import MigrationMatrix from '@/components/credit/MigrationMatrix';
import RollRateHeatmap from '@/components/credit/RollRateHeatmap';
import DataTable from '@/components/ui/DataTable';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import { useBankContext } from '@/components/shell/BankContext';
import { useCreditMigration } from '@/lib/api/hooks';
import { num } from '@/lib/api/values';
import { fmtCurrency, fmtInt } from '@/lib/format';

const parColumns: Column<PortfolioAtRiskRead>[] = [
  { key: 'label', header: 'Metric', render: (r) => <span className="text-navy">{r.label}</span> },
  {
    key: 'exposure',
    header: 'Exposure',
    align: 'right',
    numeric: true,
    render: (r) => fmtCurrency(num(r.exposureGhs)),
  },
  {
    key: 'ratio',
    header: '% of gross book',
    align: 'right',
    numeric: true,
    render: (r) => `${(num(r.ratio) * 100).toFixed(2)}%`,
  },
];

export default function CreditDelinquencyPage() {
  const { bank } = useBankContext();
  const migration = useCreditMigration(bank?.id);

  return (
    <CreditWorkspace
      crumb="Delinquency & Migration"
      subtitle="Days-past-due distribution, portfolio at risk, and month-over-month grade migration."
    >
      {({ data }) => {
        const par = Object.fromEntries(data.portfolioAtRisk.map((m) => [m.code, m]));
        const isSdi = data.institutionClass === 'sdi';
        const parCodes = isSdi
          ? ['par_30', 'par_60', 'par_90', 'par_180']
          : ['par_30', 'par_60', 'par_90'];
        const matrix = migration.data;
        return (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              {parCodes.map((code) => {
                const metric = code in par ? par[code] : undefined;
                const pct = metric ? num(metric.ratio) * 100 : null;
                return (
                  <KpiStat
                    key={code}
                    label={metric?.label ?? code}
                    value={pct != null ? `${pct.toFixed(2)}%` : '—'}
                    status={
                      pct != null && pct > 0 ? (code === 'par_30' ? 'warn' : 'crit') : undefined
                    }
                    hint="Raw DPD exposure ÷ gross loan book"
                  />
                );
              })}
            </div>

            <SectionCard title="Portfolio at risk" noPadding>
              <DataTable columns={parColumns} rows={data.portfolioAtRisk} density="compact" />
            </SectionCard>

            {matrix && matrix.available ? (
              <>
                <SectionCard
                  title="Grade migration (monthly)"
                  subtitle={`${matrix.openingAsOf} → ${matrix.asOf} · ${fmtInt(matrix.matchedLoanCount ?? 0)} matched loans, ${fmtInt(matrix.entryLoanCount ?? 0)} new, ${fmtInt(matrix.exitLoanCount ?? 0)} departed`}
                  noPadding
                  footer="Flows are measured at closing exposure; departures at their opening exposure. New loans and departures are the legs that reconcile the matrix to the month-end stocks."
                >
                  <MigrationMatrix
                    matrix={matrix.matrix ?? []}
                    entries={matrix.entries ?? []}
                    exits={matrix.exits ?? []}
                  />
                </SectionCard>
                <SectionCard
                  title="DPD roll rates"
                  subtitle="Exposure-weighted share of each opening bucket, over loans present at both month-ends."
                  noPadding
                  footer="Loans entering or leaving the book are excluded from the rates — a rate over a changing population is not a rate. Loans without a stated DPD on either date do not contribute."
                >
                  <RollRateHeatmap rollRates={matrix.rollRates ?? []} />
                </SectionCard>
              </>
            ) : matrix ? (
              <SectionCard title="Grade migration (monthly)">
                <p className="text-body text-slate leading-relaxed">{matrix.reason}</p>
              </SectionCard>
            ) : null}
          </>
        );
      }}
    </CreditWorkspace>
  );
}
