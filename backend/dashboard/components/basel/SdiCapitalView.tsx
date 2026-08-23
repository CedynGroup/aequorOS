'use client';

/**
 * The SDI simplified-capital screen (docs/sdi.md §4.2) — the s.29 view that
 * replaces the Basel 3-tier `/basel` overview for a savings-&-loans tenant:
 * CAR against the s.29 floor, the minimum paid-up-capital check, statutory-
 * reserve-fund adequacy, and NBFI loan classification / provisioning. No
 * CET1/AT1/Tier2, leverage, RWA composition or capital waterfall.
 */

import PageHeader from '@/components/ui/PageHeader';
import KpiStat, { type KpiStatus } from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { fmtFloorPct, num, numOrNull } from '@/lib/api/values';
import { fmtCurrency } from '@/lib/format';
import {
  SdiCapitalControlsChart,
  SdiCarThresholdChart,
  SdiCapitalTrendChart,
  SdiRwaCompositionChart,
} from './charts/SdiCapitalReviewCharts';
import {
  useSdiCapitalChecks,
  useSdiCapitalAssurance,
  useSdiCapitalSummary,
  useSdiLoanClassification,
  type CapitalCheck,
  type LoanGradeBucket,
  type RiskWeightBand,
} from './sdiHooks';

function kpiStatus(status: string): KpiStatus {
  return status === 'red' ? 'crit' : status === 'na' ? 'warn' : 'ok';
}

const BUCKET_LABELS: Record<string, string> = {
  cash: 'Cash & equivalents',
  sovereign: 'Government securities',
  interbank: 'Interbank placements',
  mortgage: 'Residential mortgages',
  other_loans: 'Other loans',
  other_assets: 'Other assets',
};

// The risk classes a capital ratio can charge for. The backend returns every one
// of them, in scope or not, so a ratio that covers credit risk alone says so
// here rather than leaving a reader to infer it from the bands that happen to
// be present.
const RISK_CLASS_LABELS: Record<string, string> = {
  credit: 'Credit risk',
  market: 'Market risk',
  operational: 'Operational risk',
};

function checkTone(c: CapitalCheck): 'success' | 'critical' | 'amber' | 'slate' {
  if (c.compliant === null) return 'slate';
  return c.compliant ? 'success' : 'critical';
}

function checkLabel(c: CapitalCheck): string {
  if (c.compliant === null) return 'Not computable';
  return c.compliant ? 'Compliant' : 'Below minimum';
}

const CHECK_TITLES: Record<string, string> = {
  paid_up_capital: 'Minimum paid-up capital',
  statutory_reserve_fund: 'Statutory reserve fund',
};

export default function SdiCapitalView({ bankId }: { bankId: string | undefined }) {
  const summary = useSdiCapitalSummary(bankId);
  const assurance = useSdiCapitalAssurance(bankId);
  const checks = useSdiCapitalChecks(bankId);
  const classification = useSdiLoanClassification(bankId);

  const cap = summary.data;
  // The s.29 minimum is control-plane data carried on the summary. There is
  // deliberately no `?? '10'` literal: an unresolved minimum must surface as
  // unresolved, not be invented — it drives the headroom figure, the threshold
  // lines and the compliance colour on this screen.
  const carMin = numOrNull(cap?.car_min_pct);
  const weightsPending = (cap?.pending_parameters.length ?? 0) > 0;
  // Which risk classes the ratio charges for is declared data, not an emergent
  // property of the code. Anything the ratio leaves out is named on the headline
  // and spelled out in full on the RWA card below.
  const riskClasses = cap?.risk_classes ?? [];
  const excludedRiskClasses = riskClasses.filter((rc) => !rc.in_scope);
  const chargedRiskClasses = riskClasses.filter((rc) => rc.in_scope);
  const scopeSuffix =
    chargedRiskClasses.length > 0 && excludedRiskClasses.length > 0
      ? ` · ${chargedRiskClasses
          .map((rc) => (RISK_CLASS_LABELS[rc.risk_class] ?? rc.risk_class).toLowerCase())
          .join(', ')} only`
      : '';
  const capitalHeadroom = cap?.car_pct === null || !cap || carMin === null
    ? null
    : num(cap.net_own_funds_ghs) - (num(cap.total_rwa_ghs) * carMin) / 100;
  const capitalControls = (checks.data?.checks ?? []).map((check) => ({
    label: CHECK_TITLES[check.check] ?? check.check,
    actual: check.actual_ghs === null ? null : num(check.actual_ghs),
    required: check.required_ghs === null ? null : num(check.required_ghs),
  }));
  const riskWeightBands = (cap?.bands ?? []).map((band) => ({
    label: BUCKET_LABELS[band.bucket] ?? band.bucket,
    exposure: num(band.exposure_ghs),
    rwa: num(band.rwa_ghs),
    weightPct: num(band.weight_pct),
  }));
  const assuranceHistory = (assurance.data?.history ?? []).map((point) => ({
    asOf: point.as_of,
    carPct: point.car_pct === null ? null : num(point.car_pct),
    nplPct: num(point.npl_ratio) * 100,
  }));
  const currentAssurance = assurance.data?.current;

  const bandColumns: Column<RiskWeightBand>[] = [
    {
      key: 'bucket',
      header: 'Asset class',
      render: (b) => (
        <span className="flex items-center gap-2 text-navy">
          {BUCKET_LABELS[b.bucket] ?? b.bucket}
          {b.confirmation_status === 'pending' ? (
            <StatusPill tone="amber">pending BoG</StatusPill>
          ) : null}
        </span>
      ),
    },
    {
      key: 'exposure',
      header: 'Exposure',
      numeric: true,
      render: (b) => fmtCurrency(num(b.exposure_ghs)),
    },
    {
      key: 'weight',
      header: 'Risk weight',
      numeric: true,
      render: (b) => `${num(b.weight_pct).toFixed(0)}%`,
    },
    {
      key: 'rwa',
      header: 'RWA',
      numeric: true,
      render: (b) => fmtCurrency(num(b.rwa_ghs)),
    },
  ];

  const bucketColumns: Column<LoanGradeBucket>[] = [
    { key: 'grade', header: 'Grade', render: (b) => <span className="capitalize text-navy">{b.grade}</span> },
    { key: 'count', header: 'Loans', numeric: true, render: (b) => String(b.count) },
    {
      key: 'exposure',
      header: 'Exposure',
      numeric: true,
      render: (b) => fmtCurrency(num(b.exposure_ghs)),
    },
    {
      key: 'provision',
      header: 'Provision required',
      numeric: true,
      render: (b) => fmtCurrency(num(b.provision_required_ghs)),
    },
    {
      key: 'npl',
      header: 'Non-performing',
      render: (b) =>
        b.non_performing ? (
          <StatusPill tone="critical">NPL</StatusPill>
        ) : (
          <span className="text-slate-light">—</span>
        ),
    },
  ];

  return (
    <div className="space-y-6 p-6">
      <PageHeader
        title="Regulatory Capital"
        subtitle="Simplified capital adequacy for a specialised deposit-taking institution under Act 930, Section 29."
      />

      <QueryBoundary
        isLoading={summary.isLoading}
        error={summary.error}
        onRetry={() => summary.refetch()}
      >
        {cap ? (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <KpiStat
              label="Capital Adequacy Ratio"
              value={cap.car_pct !== null ? num(cap.car_pct).toFixed(2) : '—'}
              unit={cap.car_pct !== null ? '%' : undefined}
              status={kpiStatus(cap.status)}
              hint={
                carMin === null
                  ? `s.29 minimum not resolved — ratio shown without a compliance verdict${scopeSuffix}`
                  : weightsPending
                    ? `s.29 minimum ${fmtFloorPct(carMin)} · risk weights pending confirmation${scopeSuffix}`
                    : `s.29 minimum ${fmtFloorPct(carMin)}${scopeSuffix}`
              }
            />
            <KpiStat
              label="Capital headroom"
              value={capitalHeadroom === null ? 'Not computable' : fmtCurrency(capitalHeadroom)}
              status={capitalHeadroom === null ? 'warn' : capitalHeadroom < 0 ? 'crit' : 'ok'}
              hint={
                capitalHeadroom === null
                  ? 'Requires both a computed CAR and a resolved s.29 minimum'
                  : 'Net Own Funds less capital required at the s.29 minimum'
              }
            />
            <KpiStat
              label="NPL ratio"
              value={
                classification.data
                  ? (num(classification.data.npl_ratio) * 100).toFixed(2)
                  : '—'
              }
              unit="%"
              status="ok"
              hint="Non-performing loans ÷ total exposure"
            />
            <KpiStat
              label="Provisions required"
              value={
                classification.data
                  ? fmtCurrency(num(classification.data.total_provision_required_ghs))
                  : '—'
              }
              status="ok"
              hint="Class-aware NBFI 4-grade provisioning"
            />
          </div>
        ) : null}
      </QueryBoundary>

      {cap ? (
        <div className="grid gap-6 xl:grid-cols-2">
          <SectionCard title="CAR against statutory minimum" subtitle="Current Section 29 capital adequacy ratio and the resolved minimum.">
            {/* The chart renders nothing when there is no ratio to plot, and this
                card sits in a grid row sized by its taller sibling — so an
                unresolved CAR left a card-height void with no word of
                explanation, which reads as a broken panel rather than the
                fail-closed state it is. Say what is missing instead. */}
            {cap.car_pct === null ? (
              <p className="py-8 text-center text-body text-slate">
                No capital adequacy ratio has been computed for this period, so there is
                nothing to plot against the statutory minimum.
              </p>
            ) : (
              <SdiCarThresholdChart carPct={numOrNull(cap.car_pct)} carMinPct={carMin} />
            )}
          </SectionCard>
          <SectionCard
            title="Simplified risk-weighted assets"
            subtitle="Current eligible asset exposure and its simplified RWA contribution."
            footer={
              cap.composition_source === 'code_default' ? (
                <span className="text-caption text-warning">
                  This scope is the platform&rsquo;s documented default, not a scope approved for
                  this institution — the ratio is provisional until it is approved.
                </span>
              ) : undefined
            }
          >
            <SdiRwaCompositionChart data={riskWeightBands} />
            {riskClasses.length > 0 ? (
              <div className="mt-5 border-t border-border-light pt-4">
                <p className="text-caption font-medium uppercase tracking-wider text-slate-light">
                  What this ratio charges for
                </p>
                <p className="mt-1 text-body text-slate">{cap.rwa_scope_note}</p>
                <ul className="mt-3 space-y-3">
                  {riskClasses.map((rc) => (
                    <li key={rc.risk_class} className="flex items-start gap-3">
                      <StatusPill tone={rc.in_scope ? 'success' : 'amber'}>
                        {rc.in_scope ? 'Charged' : 'Not charged'}
                      </StatusPill>
                      <div className="min-w-0">
                        <p className="text-body text-navy">
                          {RISK_CLASS_LABELS[rc.risk_class] ?? rc.risk_class}
                          {rc.in_scope ? ` — ${fmtCurrency(num(rc.rwa_ghs))}` : ''}
                        </p>
                        <p className="text-caption text-slate">{rc.note}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </SectionCard>
        </div>
      ) : null}

      <QueryBoundary
        isLoading={assurance.isLoading}
        error={assurance.error}
        onRetry={() => assurance.refetch()}
      >
        {assurance.data && currentAssurance ? (
          <>
            <div className="grid gap-6 xl:grid-cols-2">
              <SectionCard title="Capital and asset-quality trajectory" subtitle="Historical points are shown only where the accepted position book and capital structure share an as-of date.">
                <SdiCapitalTrendChart data={assuranceHistory} carMinPct={carMin} />
              </SectionCard>
              <SectionCard
                title="Reconciliation evidence"
                subtitle="A filing review needs booked values and a traceable GL comparison, not required provisions alone."
              >
                <dl className="grid grid-cols-2 gap-x-5 gap-y-4 text-caption">
                  <div>
                    <dt className="text-slate">Mapped GL capital</dt>
                    <dd className="mt-1 text-body font-medium text-navy">
                      {assurance.data.mapped_gl_capital_ghs === null ? 'Not mapped' : fmtCurrency(num(assurance.data.mapped_gl_capital_ghs))}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate">Capital to GL difference</dt>
                    <dd className="mt-1 text-body font-medium text-navy">
                      {assurance.data.capital_to_gl_difference_ghs === null ? 'Not computable' : fmtCurrency(num(assurance.data.capital_to_gl_difference_ghs))}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate">Booked ECL provision</dt>
                    <dd className="mt-1 text-body font-medium text-navy">
                      {currentAssurance.actual_provision_ghs === null ? 'Not supplied' : fmtCurrency(num(currentAssurance.actual_provision_ghs))}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate">Provision coverage of NPLs</dt>
                    <dd className="mt-1 text-body font-medium text-navy">
                      {currentAssurance.provision_coverage_pct === null ? 'Not computable' : `${num(currentAssurance.provision_coverage_pct).toFixed(2)}%`}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate">Statutory reserve movement</dt>
                    <dd className="mt-1 text-body font-medium text-navy">
                      {assurance.data.reserve_change_ghs === null ? 'No prior period' : fmtCurrency(num(assurance.data.reserve_change_ghs))}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate">Calculation status</dt>
                    <dd className="mt-1">
                      <StatusPill tone={currentAssurance.assessment_status === 'provisional' ? 'amber' : currentAssurance.assessment_status === 'not_computable' ? 'critical' : 'slate'}>
                        {currentAssurance.assessment_status.replaceAll('_', ' ')}
                      </StatusPill>
                    </dd>
                  </div>
                </dl>
              </SectionCard>
            </div>
            <SectionCard title="Filing readiness" subtitle="This control remains blocked until calculation evidence and the prescribed BoG return template have been verified.">
              <div className="flex items-center gap-3">
                <StatusPill tone="critical">Not filing-ready</StatusPill>
                <span className="text-caption text-slate">{assurance.data.filing_blockers.length} evidence blocker(s)</span>
              </div>
              <ul className="mt-4 list-disc space-y-2 pl-5 text-caption text-slate">
                {assurance.data.filing_blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
              </ul>
            </SectionCard>
          </>
        ) : null}
      </QueryBoundary>

      <SectionCard
        title="Capital adequacy"
        subtitle="Capital adequacy ratio = Net Own Funds ÷ Risk-Weighted Assets, using the simplified specialised-deposit-taking risk weights."
        noPadding
      >
        <QueryBoundary
          isLoading={summary.isLoading}
          error={summary.error}
          onRetry={() => summary.refetch()}
        >
          {cap ? (
            <div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-border-light">
                <div className="bg-surface p-4">
                  <p className="text-caption text-slate">Net Own Funds</p>
                  <p className="mt-1 text-body font-semibold text-navy">
                    {fmtCurrency(num(cap.net_own_funds_ghs))}
                  </p>
                </div>
                <div className="bg-surface p-4">
                  <p className="text-caption text-slate">Risk-Weighted Assets</p>
                  <p className="mt-1 text-body font-semibold text-navy">
                    {fmtCurrency(num(cap.total_rwa_ghs))}
                  </p>
                </div>
                <div className="bg-surface p-4">
                  <p className="text-caption text-slate">CAR</p>
                  <p className="mt-1 text-body font-semibold text-navy">
                    {cap.car_pct !== null ? `${num(cap.car_pct).toFixed(2)}%` : '—'}
                  </p>
                </div>
                <div className="bg-surface p-4">
                  <p className="text-caption text-slate">s.29 minimum</p>
                  <p className="mt-1 text-body font-semibold text-navy">
                    {carMin === null ? 'not resolved' : fmtFloorPct(carMin)}
                  </p>
                </div>
              </div>
              <DataTable columns={bandColumns} rows={cap.bands} density="compact" />
            </div>
          ) : null}
        </QueryBoundary>
      </SectionCard>

      {(checks.data?.checks.length ?? 0) > 0 ? (
        <SectionCard title="Capital control headroom" subtitle="Actual paid-up capital and reserve fund against their current requirements.">
          <SdiCapitalControlsChart data={capitalControls} />
        </SectionCard>
      ) : null}

      <SectionCard
        title="Simplified-capital checks"
        subtitle="Minimum paid-up capital and statutory reserve fund."
      >
        <QueryBoundary
          isLoading={checks.isLoading}
          error={checks.error}
          onRetry={() => checks.refetch()}
        >
          <div className="divide-y divide-border-light">
            {(checks.data?.checks ?? []).map((c) => (
              <div key={c.check} className="flex items-start justify-between gap-4 p-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-body font-medium text-navy">
                      {CHECK_TITLES[c.check] ?? c.check}
                    </span>
                    {c.confirmation_status === 'pending' ? (
                      <StatusPill tone="amber">pending BoG</StatusPill>
                    ) : null}
                  </div>
                  <p className="mt-1 text-caption text-slate">{c.detail}</p>
                  <p className="mt-1 text-micro text-slate-light">{c.source_citation}</p>
                </div>
                <StatusPill tone={checkTone(c)}>{checkLabel(c)}</StatusPill>
              </div>
            ))}
          </div>
        </QueryBoundary>
      </SectionCard>

      <SectionCard
        title="Loan classification & provisioning"
        subtitle="NBFI 4-grade (Standard / Sub-standard / Doubtful / Loss); NPL at 90 days past due"
        noPadding
      >
        <QueryBoundary
          isLoading={classification.isLoading}
          error={classification.error}
          onRetry={() => classification.refetch()}
        >
          {classification.data ? (
            <DataTable
              columns={bucketColumns}
              rows={classification.data.buckets}
              density="compact"
            />
          ) : null}
        </QueryBoundary>
      </SectionCard>
    </div>
  );
}
