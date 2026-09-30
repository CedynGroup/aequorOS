'use client';

/**
 * Loan-book activity: restructures, write-offs and recoveries over the
 * trailing year — the events behind the monthly asset-quality report.
 * Ships with an honest empty state until loan events are ingested.
 *
 * EVERY COUNT HERE IS OVER THE READER'S OWN SLICE. This is the second of only two
 * surfaces that serve a branch- or region-scoped grant instead of refusing it: an
 * event is attributed through the facility it names and that facility's latest
 * computed position on or before the event date, so a scoped reader is shown the
 * events of their own branches and the two counts are computed over them. The
 * payload's `data_scope` is therefore stated on screen — and the empty state has
 * to say which emptiness it means, because "no loan events recorded yet" is a
 * claim about the institution that a scoped reader is in no position to make.
 */

import { FileClock } from 'lucide-react';
import Link from 'next/link';
import type { Column } from '@/components/ui/DataTable';
import type { LoanEventRead } from '@aequoros/risk-service-api';
import CoverageNotice from '@/components/access/CoverageNotice';
import CreditWorkspace from '@/components/credit/CreditWorkspace';
import DataTable from '@/components/ui/DataTable';
import EmptyState from '@/components/ui/EmptyState';
import KpiStat from '@/components/ui/KpiStat';
import QueryBoundary from '@/components/ui/QueryBoundary';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import { useBankContext } from '@/components/shell/BankContext';
import { coverageFigureLabel, coverageFromPayload } from '@/lib/api/dataScope';
import { useCreditActivity } from '@/lib/api/hooks';
import { labelize, num } from '@/lib/api/values';
import { centralBankName, fmtCurrency, fmtInt } from '@/lib/format';

function eventColumns(kind: 'restructure' | 'write_off' | 'recovery'): Column<LoanEventRead>[] {
  const base: Column<LoanEventRead>[] = [
    {
      key: 'ref',
      header: 'Event',
      render: (r) => <span className="font-mono text-caption text-navy">{r.sourceReference}</span>,
    },
    {
      key: 'facility',
      header: 'Facility',
      render: (r) => (
        <span className="font-mono text-caption text-navy/85">{r.positionSourceReference}</span>
      ),
    },
    { key: 'date', header: 'Date', render: (r) => <span className="font-mono">{r.eventDate}</span> },
    {
      key: 'subtype',
      header: kind === 'restructure' ? 'Measure' : 'Classification',
      render: (r) =>
        r.eventSubtype ? (
          <StatusPill tone={r.eventSubtype === 'wilful' ? 'critical' : 'slate'}>
            {labelize(r.eventSubtype)}
          </StatusPill>
        ) : (
          '—'
        ),
    },
    {
      key: 'amount',
      header: 'Amount',
      align: 'right',
      numeric: true,
      render: (r) =>
        r.amountGhs != null ? (
          fmtCurrency(num(r.amountGhs))
        ) : (
          <span title="Foreign-currency event without a stated conversion">
            {`${r.amount} ${r.currency}`}
          </span>
        ),
    },
  ];
  return base;
}

export default function CreditActivityPage() {
  const { bank } = useBankContext();
  const activity = useCreditActivity(bank?.id);
  // The scope the SERVER applied to these events and both counts. Authoritative;
  // absent means undeterminable, never the whole institution.
  const coverage = coverageFromPayload(activity.data, {
    pending: activity.isLoading,
  });

  return (
    <CreditWorkspace
    >
      {() => (
        <QueryBoundary
          contained
          isLoading={activity.isLoading}
          error={activity.error}
          onRetry={() => activity.refetch()}
        >
          {activity.data
            ? (() => {
                const data = activity.data;
                const empty =
                  data.restructures.length === 0 &&
                  data.writeOffs.length === 0 &&
                  data.recoveries.length === 0 &&
                  data.disbursementCount === 0 &&
                  data.repaymentCount === 0;
                const writeOffTotal = data.writeOffs.reduce(
                  (sum, event) => sum + (event.amountGhs != null ? num(event.amountGhs) : 0),
                  0
                );
                const recoveryTotal = data.recoveries.reduce(
                  (sum, event) => sum + (event.amountGhs != null ? num(event.amountGhs) : 0),
                  0
                );
                return (
                  <>
                    <CoverageNotice coverage={coverage} />
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                      <KpiStat
                        label={coverageFigureLabel('Restructures (12m)', coverage)}
                        value={fmtInt(data.restructures.length)}
                        hint={coverage.hint ?? undefined}
                      />
                      <KpiStat
                        label={coverageFigureLabel('Write-offs (12m)', coverage)}
                        value={empty ? '—' : fmtCurrency(writeOffTotal)}
                        hint={
                          empty
                            ? 'No events ingested yet'
                            : `${fmtInt(data.writeOffs.length)} events`
                        }
                      />
                      <KpiStat
                        label={coverageFigureLabel('Recoveries (12m)', coverage)}
                        value={empty ? '—' : fmtCurrency(recoveryTotal)}
                        hint={
                          empty
                            ? 'No events ingested yet'
                            : `${fmtInt(data.recoveries.length)} events`
                        }
                      />
                      <KpiStat
                        label={coverageFigureLabel('Disbursements / repayments', coverage)}
                        value={
                          empty
                            ? '—'
                            : `${fmtInt(data.disbursementCount)} / ${fmtInt(data.repaymentCount)}`
                        }
                        hint="Event counts in the window"
                      />
                    </div>

                    {empty ? (
                      <EmptyState
                        Icon={FileClock}
                        title={
                          coverage.qualified
                            ? 'No loan events in the part of the book you can see'
                            : 'No loan events recorded yet'
                        }
                        description={
                          coverage.emptyMeaning ??
                          "Restructure, write-off and recovery events populate this page once loan events are ingested. Until then the book's stock measures live on the Overview tab."
                        }
                        action={
                          coverage.qualified ? undefined : (
                            <Link href="/data-engine" className="btn-primary">
                              Open the Data Engine
                            </Link>
                          )
                        }
                      />
                    ) : (
                      <>
                        <SectionCard
                          title="Restructured facilities"
                          subtitle="A restructured facility remains non-performing until the required consecutive full repayments are made."
                          noPadding
                        >
                          {data.restructures.length > 0 ? (
                            <DataTable
                              columns={eventColumns('restructure')}
                              rows={data.restructures}
                              density="compact"
                            />
                          ) : (
                            <p className="p-5 text-body text-slate">
                              No restructures in the trailing year.
                            </p>
                          )}
                        </SectionCard>
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                          <SectionCard title="Write-offs" noPadding>
                            {data.writeOffs.length > 0 ? (
                              <DataTable
                                columns={eventColumns('write_off')}
                                rows={data.writeOffs}
                                density="compact"
                              />
                            ) : (
                              <p className="p-5 text-body text-slate">
                                No write-offs in the trailing year.
                              </p>
                            )}
                          </SectionCard>
                          <SectionCard title="Recoveries" noPadding>
                            {data.recoveries.length > 0 ? (
                              <DataTable
                                columns={eventColumns('recovery')}
                                rows={data.recoveries}
                                density="compact"
                              />
                            ) : (
                              <p className="p-5 text-body text-slate">
                                No recoveries in the trailing year.
                              </p>
                            )}
                          </SectionCard>
                        </div>
                      </>
                    )}
                  </>
                );
              })()
            : null}
        </QueryBoundary>
      )}
    </CreditWorkspace>
  );
}
