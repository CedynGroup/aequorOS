'use client';

import { Download, FileSpreadsheet, Info, Loader2, PlayCircle } from 'lucide-react';
import type {
  Bsd3RowRead,
  Bsd3SummaryRowRead,
  Bsd3WeightedRowRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import ValidationList from '@/components/ui/ValidationList';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isNoBaselineRunError,
  useBsd3Preview,
  useCreateRegulatoryRun,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import {
  fmtDateUTC,
  fmtTimestamp,
  labelize,
  num,
  shortId,
} from '@/lib/api/values';
import { fmtCurrencyFull } from '@/lib/format';

export default function SubmissionPreview() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const preview = useBsd3Preview(bankId, periodId);
  const run = useRegulatoryRun(bankId, preview.data?.runId);
  const runBaseline = useCreateRegulatoryRun(bankId);

  const data = preview.data;
  const needsBaseline = isNoBaselineRunError(preview.error);

  const runBaselineButton = (
    <button
      type="button"
      disabled={runBaseline.isPending || !periodId}
      onClick={() =>
        periodId &&
        runBaseline.mutate({
          module: 'liquidity',
          reportingPeriodId: periodId,
          scenarioCode: 'baseline',
        })
      }
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
    >
      {runBaseline.isPending ? (
        <Loader2 size={13} className="animate-spin" aria-hidden />
      ) : (
        <PlayCircle size={13} aria-hidden />
      )}
      Run baseline calculations
    </button>
  );

  return (
    <>
      {/* Page chrome is hidden when printing — only the return itself prints. */}
      <div className="no-print">
        <PageHeader
          breadcrumbs={[
            { label: 'Modules', href: '/' },
            { label: 'Liquidity Risk', href: '/liquidity' },
            { label: 'BoG Submission' },
          ]}
          title={`BoG Liquidity Return — ${period?.label ?? ''}`}
          subtitle={
            data
              ? `Form ${data.header.formCode} · ${data.header.formTitle}`
              : 'Form BSD-3 · Liquidity Returns (LCR & NSFR)'
          }
          asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
          action={
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => window.print()}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface"
              >
                <Download size={13} aria-hidden /> Print / PDF
              </button>
              <button
                type="button"
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-action border border-action/30 bg-action-light rounded-md hover:bg-action/10"
              >
                <FileSpreadsheet size={13} aria-hidden /> Export to BoG Excel
              </button>
            </div>
          }
        />
      </div>

      {needsBaseline ? (
        <div className="px-8 py-6">
          <EmptyState
            Icon={PlayCircle}
            title="Baseline run required"
            description="A successful baseline liquidity run is required before the BSD-3 preview can be generated for this reporting period."
            action={
              <div className="flex flex-col items-center gap-3">
                {runBaselineButton}
                {runBaseline.error && (
                  <ErrorPanel error={runBaseline.error} title="Run failed" />
                )}
              </div>
            }
          />
        </div>
      ) : (
        <QueryBoundary
          isLoading={preview.isLoading}
          error={preview.error}
          onRetry={() => preview.refetch()}
        >
          {data && (
            <div className="px-8 py-6 space-y-6 print:p-0 print:space-y-0">
              {/* Preview banner — API note rendered verbatim */}
              <div className="no-print card border-l-4 border-l-action bg-action-light/40 p-5 flex items-start gap-3">
                <Info size={18} className="text-action shrink-0 mt-0.5" aria-hidden />
                <div>
                  <p className="text-body font-medium text-navy">
                    {data.header.previewNote}
                  </p>
                  <p className="mt-1 text-body text-navy/80 leading-relaxed">
                    Generated from the latest successful baseline liquidity run
                    for {data.header.reportingPeriodLabel}.{' '}
                    {data.validations.filter((v) => v.passed).length} of{' '}
                    {data.validations.length} validation checks passed.
                  </p>
                </div>
              </div>

              {/* The regulatory document — the only element that prints */}
              <div className="bg-surface-raised border border-border rounded-md shadow-subtle print:border-0 print:shadow-none print:rounded-none">
                {/* Form header */}
                <div className="px-8 py-6 border-b border-border-light">
                  <div className="flex items-start justify-between gap-6 flex-wrap">
                    <div>
                      <p className="text-micro font-medium uppercase tracking-wider text-slate">
                        {data.header.regulator} · Banking Supervision Department
                      </p>
                      <h2 className="mt-2 text-h1 text-navy">
                        {data.header.formTitle}
                      </h2>
                      <p className="mt-1 text-body text-slate">
                        Form {data.header.formCode} · Reporting period:{' '}
                        {data.header.reportingPeriodLabel} · Period end{' '}
                        {fmtDateUTC(data.header.periodEnd)}
                      </p>
                    </div>
                    <div className="text-right text-caption text-slate space-y-1">
                      <p>
                        Reporting bank{' '}
                        <span className="font-medium text-navy">
                          {data.header.bankName}
                        </span>
                      </p>
                      <p>
                        License class{' '}
                        <span className="font-mono text-navy">
                          {labelize(data.header.licenseType)}
                        </span>
                      </p>
                      <p>
                        Currency unit:{' '}
                        <span className="font-mono text-navy">
                          {data.header.currency}, full amounts
                        </span>
                      </p>
                      <p>
                        Generated at:{' '}
                        <span className="font-mono text-navy">
                          {fmtTimestamp(data.header.generatedAt)}
                        </span>
                      </p>
                    </div>
                  </div>
                </div>

                {/* Form body */}
                <div className="px-8 py-6 space-y-8">
                  <AmountTable
                    caption="Section 1 — High quality liquid assets"
                    rows={data.hqlaRows}
                  />
                  <WeightedTable
                    caption="Section 4 — Cash outflows (30 days)"
                    rateHeader="Runoff %"
                    rows={data.outflowRows}
                  />
                  <WeightedTable
                    caption="Section 6 — Cash inflows (30 days)"
                    rateHeader="Inflow %"
                    rows={data.inflowRows}
                  />
                  <SummaryTable
                    caption="LCR summary"
                    rows={data.summaryRows}
                  />
                  <WeightedTable
                    caption="Section 10 — Available stable funding (ASF)"
                    rateHeader="ASF factor %"
                    rows={data.nsfr.asfRows}
                    totalRow={data.nsfr.asfTotal}
                  />
                  <WeightedTable
                    caption="Section 12 — Required stable funding (RSF)"
                    rateHeader="RSF factor %"
                    rows={data.nsfr.rsfRows}
                    totalRow={data.nsfr.rsfTotal}
                  />
                  <SummaryTable
                    caption="NSFR summary"
                    rows={[data.nsfr.nsfrRatio]}
                  />
                </div>

                {/* Certification footer */}
                <div className="px-8 py-5 border-t border-border-light bg-surface flex items-start justify-between flex-wrap gap-4">
                  <div className="text-caption text-slate space-y-1 max-w-2xl">
                    <p>
                      <span className="font-medium text-navy">Certification:</span>{' '}
                      This return must be certified as true and complete, and
                      prepared in accordance with Bank of Ghana directives,
                      before any filing. This preview is generated for internal
                      review only.
                    </p>
                    {run.data && (
                      <p>
                        Generated by{' '}
                        <span className="font-mono text-navy">
                          {run.data.engineVersion}
                        </span>{' '}
                        · Run{' '}
                        <span className="font-mono text-navy">
                          {shortId(data.runId)}
                        </span>{' '}
                        · Input hash{' '}
                        <span className="font-mono text-navy">
                          {shortId(run.data.inputHash, 12)}
                        </span>
                      </p>
                    )}
                  </div>
                  <StatusPill tone="action">Preview</StatusPill>
                </div>
              </div>

              {/* Validation checks */}
              <Card className="no-print">
                <CardHeader
                  title="Validation checks"
                  subtitle="Regulatory rule evaluation for this return"
                />
                <CardBody className="p-0">
                  <ValidationList validations={data.validations} />
                </CardBody>
              </Card>

              {/* Audit trail — stored run metadata */}
              <Card className="no-print">
                <CardHeader
                  title="Audit trail"
                  subtitle="Immutable metadata of the baseline run behind this preview"
                />
                <CardBody className="p-0">
                  {run.isLoading ? (
                    <p className="px-5 py-4 text-body text-slate">
                      Loading run metadata…
                    </p>
                  ) : run.data ? (
                    <ul className="divide-y divide-border-light">
                      <AuditRow
                        label="Run"
                        value={`${shortId(run.data.id)} · ${labelize(
                          run.data.scenarioCode
                        )} · ${labelize(run.data.module ?? 'liquidity')}`}
                        pill={
                          <StatusPill
                            tone={
                              run.data.status === 'succeeded'
                                ? 'success'
                                : 'critical'
                            }
                          >
                            {labelize(run.data.status)}
                          </StatusPill>
                        }
                      />
                      <AuditRow
                        label="Engine"
                        value={run.data.engineVersion}
                      />
                      <AuditRow
                        label="Input hash"
                        value={shortId(run.data.inputHash, 16)}
                      />
                      <AuditRow
                        label="Created"
                        value={`${fmtTimestamp(run.data.createdAt)} · by ${shortId(
                          run.data.createdBy
                        )}`}
                      />
                    </ul>
                  ) : (
                    <p className="px-5 py-4 text-body text-slate">
                      Run metadata unavailable.
                    </p>
                  )}
                </CardBody>
              </Card>
            </div>
          )}
        </QueryBoundary>
      )}
    </>
  );
}

function SectionCaption({ children }: { children: string }) {
  return (
    <p className="text-caption font-medium uppercase tracking-wider text-slate mb-2">
      {children}
    </p>
  );
}

const thClass =
  'py-2 text-micro font-medium uppercase tracking-wider text-slate';

function AmountTable({ caption, rows }: { caption: string; rows: Bsd3RowRead[] }) {
  return (
    <div>
      <SectionCaption>{caption}</SectionCaption>
      <table className="w-full text-body border-collapse">
        <colgroup>
          <col style={{ width: '64px' }} />
          <col />
          <col style={{ width: '180px' }} />
        </colgroup>
        <thead>
          <tr className="border-b-2 border-navy">
            <th className={`text-left ${thClass}`}>Row</th>
            <th className={`text-left ${thClass}`}>Description</th>
            <th className={`text-right ${thClass}`}>Amount (GHS)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.rowCode} className="border-b border-border-light">
              <td className="py-2.5 font-mono text-caption text-slate align-top">
                {row.rowCode}
              </td>
              <td className="py-2.5 align-top text-navy/90">{row.description}</td>
              <td className="py-2.5 num align-top">
                <span className="font-mono tabular-nums text-navy/90">
                  {fmtCurrencyFull(num(row.amount), 'GHS')}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WeightedTable({
  caption,
  rateHeader,
  rows,
  totalRow,
}: {
  caption: string;
  rateHeader: string;
  rows: Bsd3WeightedRowRead[];
  totalRow?: Bsd3SummaryRowRead;
}) {
  return (
    <div>
      <SectionCaption>{caption}</SectionCaption>
      <table className="w-full text-body border-collapse">
        <colgroup>
          <col style={{ width: '64px' }} />
          <col />
          <col style={{ width: '150px' }} />
          <col style={{ width: '90px' }} />
          <col style={{ width: '150px' }} />
        </colgroup>
        <thead>
          <tr className="border-b-2 border-navy">
            <th className={`text-left ${thClass}`}>Row</th>
            <th className={`text-left ${thClass}`}>Description</th>
            <th className={`text-right ${thClass}`}>Balance (GHS)</th>
            <th className={`text-right ${thClass}`}>{rateHeader}</th>
            <th className={`text-right ${thClass}`}>Weighted (GHS)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.rowCode} className="border-b border-border-light">
              <td className="py-2.5 font-mono text-caption text-slate align-top">
                {row.rowCode}
              </td>
              <td className="py-2.5 align-top text-navy/90">{row.description}</td>
              <td className="py-2.5 num align-top">
                <span className="font-mono tabular-nums text-navy/90">
                  {fmtCurrencyFull(num(row.balance), 'GHS')}
                </span>
              </td>
              <td className="py-2.5 num align-top">
                <span className="font-mono tabular-nums text-navy/90">
                  {num(row.ratePct).toFixed(1)}%
                </span>
              </td>
              <td className="py-2.5 num align-top">
                <span className="font-mono tabular-nums text-navy/90">
                  {fmtCurrencyFull(num(row.weightedAmount), 'GHS')}
                </span>
              </td>
            </tr>
          ))}
          {totalRow && (
            <tr className="border-b border-navy bg-surface font-medium">
              <td className="py-2.5 font-mono text-caption text-slate align-top">
                {totalRow.rowCode}
              </td>
              <td className="py-2.5 align-top text-navy font-medium uppercase tracking-wide text-caption">
                {totalRow.description}
              </td>
              <td className="py-2.5" />
              <td className="py-2.5" />
              <td className="py-2.5 num align-top">
                <span className="font-mono tabular-nums text-navy text-h3">
                  {fmtCurrencyFull(num(totalRow.value), 'GHS')}
                </span>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function SummaryTable({
  caption,
  rows,
}: {
  caption: string;
  rows: Bsd3SummaryRowRead[];
}) {
  return (
    <div>
      <SectionCaption>{caption}</SectionCaption>
      <table className="w-full text-body border-collapse">
        <colgroup>
          <col style={{ width: '64px' }} />
          <col />
          <col style={{ width: '180px' }} />
        </colgroup>
        <tbody>
          {rows.map((row) => (
            <tr key={row.rowCode} className="border-b border-navy bg-surface font-medium">
              <td className="py-2.5 font-mono text-caption text-slate align-top">
                {row.rowCode}
              </td>
              <td className="py-2.5 align-top text-navy font-medium uppercase tracking-wide text-caption">
                {row.description}
              </td>
              <td className="py-2.5 num align-top">
                <span className="font-mono text-navy text-h3 tabular-nums">
                  {row.unit === 'pct'
                    ? `${num(row.value).toFixed(2)}%`
                    : fmtCurrencyFull(num(row.value), 'GHS')}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AuditRow({
  label,
  value,
  pill,
}: {
  label: string;
  value: string;
  pill?: React.ReactNode;
}) {
  return (
    <li className="px-5 py-3 flex items-center gap-4 text-caption">
      <span className="font-medium text-navy w-32 shrink-0 uppercase tracking-wider text-micro">
        {label}
      </span>
      <span className="font-mono text-navy/85 flex-1 min-w-0 truncate tabular-nums">
        {value}
      </span>
      {pill && <span className="shrink-0">{pill}</span>}
    </li>
  );
}
