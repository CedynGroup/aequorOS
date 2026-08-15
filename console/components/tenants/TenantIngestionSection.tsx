'use client';

import { useState } from 'react';
import type {
  ApiError,
  TenantIngestionBatch,
  TenantIngestionListResponse,
  TenantTranslationFailure,
} from '@/lib/api';
import { getTenantIngestionBatch } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, fmtTs, relTime, shortId, DASH } from '@/lib/format';
import {
  Chip,
  DataTable,
  Drawer,
  EmptyState,
  ErrorPanel,
  FieldRow,
  SectionCard,
  SkeletonRows,
  StatusChip,
  type Column,
} from '@/components/ui';
import { DeepSectionBody } from './DeepSectionBody';
import { formatJson, isInspectionRequired } from './util';

/**
 * Recent ingestion batches for one tenant, with a row → Drawer detail that
 * surfaces the validation/ETL reports and the per-row translation failures
 * (each with its raw source record) — the "why did this upload fail" view.
 */

/** Number with a subdued colour only when non-zero (0 stays neutral). */
function Count({ value, tone }: { value: number; tone?: 'warn' | 'crit' }) {
  if (!value) return <span className="text-slate-light">0</span>;
  const cls = tone === 'crit' ? 'text-critical' : tone === 'warn' ? 'text-warning' : 'text-navy';
  return <span className={`font-mono ${cls}`}>{value.toLocaleString()}</span>;
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-72 overflow-auto rounded border border-border-light bg-surface p-3 font-mono text-caption leading-relaxed text-navy/90">
      {formatJson(value)}
    </pre>
  );
}

function TranslationFailureCard({ f }: { f: TenantTranslationFailure }) {
  return (
    <div className="rounded-md border border-border-light p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Chip mono>{f.entity_type}</Chip>
          <Chip tone="crit" mono title={f.error_code}>
            {f.error_code}
          </Chip>
        </div>
        <span className="font-mono text-micro text-slate" title={fmtTs(f.created_at)}>
          {relTime(f.created_at)}
        </span>
      </div>
      <p className="mt-2 break-words text-caption text-navy/90">{f.error_message}</p>
      <p className="mt-1 font-mono text-micro text-slate">at {f.source_locator}</p>
      <details className="mt-2">
        <summary className="cursor-pointer text-micro font-medium uppercase tracking-wider text-slate hover:text-navy">
          Raw record
        </summary>
        <div className="mt-2">
          <JsonBlock value={f.raw_record} />
        </div>
      </details>
    </div>
  );
}

function BatchDetailDrawer({
  orgId,
  orgLabel,
  batchId,
  onClose,
}: {
  orgId: string;
  orgLabel?: string;
  batchId: string | null;
  onClose: () => void;
}) {
  const detail = useApi(
    () => (batchId ? getTenantIngestionBatch(orgId, batchId) : Promise.resolve(null)),
    [orgId, batchId],
  );
  const d = detail.data;

  return (
    <Drawer
      open={Boolean(batchId)}
      onClose={onClose}
      title="Ingestion batch"
      description={batchId ? shortId(batchId, 12) : undefined}
      width="w-[560px]"
    >
      {detail.loading && !d ? (
        <SkeletonRows rows={6} />
      ) : detail.error && isInspectionRequired(detail.error) ? (
        <EmptyState
          title="Inspection session ended"
          description="Re-open an inspection session to view this batch's detail."
        />
      ) : detail.error ? (
        <ErrorPanel error={detail.error} onRetry={detail.reload} context="Loading batch" />
      ) : d ? (
        <div className="space-y-5">
          <div className="grid gap-x-8 sm:grid-cols-2">
            <FieldRow label="Status">
              <StatusChip value={d.batch.status} />
            </FieldRow>
            <FieldRow label="Source">
              <span className="font-mono">{d.batch.source_system}</span>
            </FieldRow>
            <FieldRow label="As of">{fmtDate(d.batch.as_of_date)}</FieldRow>
            <FieldRow label="Extraction">
              <span className="font-mono text-caption">{d.batch.extraction_mode}</span>
            </FieldRow>
            <FieldRow label="Adapter">
              <span className="font-mono text-caption">{d.batch.adapter_version}</span>
            </FieldRow>
            <FieldRow label="Bank">
              <span className="font-mono text-caption">{d.batch.bank_id}</span>
            </FieldRow>
            <FieldRow label="Completed">
              <span title={fmtTs(d.batch.completed_at)}>{relTime(d.batch.completed_at)}</span>
            </FieldRow>
            <FieldRow label="Created">
              <span title={fmtTs(d.batch.created_at)}>{relTime(d.batch.created_at)}</span>
            </FieldRow>
          </div>

          <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
            <RecordStat label="Extracted" value={d.batch.records_extracted} />
            <RecordStat label="Translated" value={d.batch.records_translated} />
            <RecordStat label="Accepted" value={d.batch.records_accepted} />
            <RecordStat label="Warning" value={d.batch.records_warning} tone="warn" />
            <RecordStat label="Error" value={d.batch.records_error} tone="crit" />
            <RecordStat label="Blocked" value={d.batch.records_blocked} tone="crit" />
          </div>

          {(d.batch.error_code || d.batch.error_message) && (
            <div className="rounded-md border border-critical/40 bg-critical-light p-3">
              {d.batch.error_code && (
                <span className="font-mono text-caption font-medium text-critical">
                  {d.batch.error_code}
                </span>
              )}
              {d.batch.error_message && (
                <p className="mt-1 break-words text-caption text-critical">{d.batch.error_message}</p>
              )}
            </div>
          )}

          <div>
            <h4 className="mb-2 text-micro font-medium uppercase tracking-wider text-slate">
              Translation failures ({d.translation_failures.length})
            </h4>
            {d.translation_failures.length > 0 ? (
              <div className="space-y-2">
                {d.translation_failures.map((f, i) => (
                  <TranslationFailureCard key={`${f.source_locator}-${i}`} f={f} />
                ))}
              </div>
            ) : (
              <p className="text-caption text-slate">No per-row translation failures recorded.</p>
            )}
          </div>

          <div>
            <h4 className="mb-2 text-micro font-medium uppercase tracking-wider text-slate">
              Validation report
            </h4>
            <JsonBlock value={d.validation_report} />
          </div>

          {d.etl_report && (
            <div>
              <h4 className="mb-2 text-micro font-medium uppercase tracking-wider text-slate">
                ETL report
              </h4>
              <JsonBlock value={d.etl_report} />
            </div>
          )}
        </div>
      ) : (
        <EmptyState title="No batch selected" />
      )}
    </Drawer>
  );
}

function RecordStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: 'warn' | 'crit';
}) {
  return (
    <div className="rounded border border-border-light px-2 py-1.5">
      <div className="text-micro uppercase tracking-wider text-slate">{label}</div>
      <div className="text-body">
        <Count value={value} tone={tone} />
      </div>
    </div>
  );
}

export function TenantIngestionSection({
  data,
  loading,
  error,
  reload,
  orgId,
  orgLabel,
}: {
  data: TenantIngestionListResponse | null;
  loading: boolean;
  error: ApiError | null;
  reload: () => void;
  orgId: string;
  orgLabel?: string;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const batches = data?.batches ?? [];

  const columns: Column<TenantIngestionBatch>[] = [
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortAccessor: (b) => b.status,
      render: (b) => <StatusChip value={b.status} />,
    },
    {
      key: 'source',
      header: 'Source',
      sortable: true,
      sortAccessor: (b) => b.source_system,
      render: (b) => (
        <div className="min-w-0">
          <div className="font-mono text-caption text-navy">{b.source_system}</div>
          <div className="font-mono text-micro text-slate">{b.extraction_mode}</div>
        </div>
      ),
    },
    {
      key: 'as_of',
      header: 'As of',
      sortable: true,
      sortAccessor: (b) => b.as_of_date,
      render: (b) => <span className="text-caption text-navy/90">{fmtDate(b.as_of_date)}</span>,
    },
    {
      key: 'records',
      header: 'Records',
      render: (b) => (
        <span className="font-mono text-caption">
          <Count value={b.records_accepted} /> ok
          {b.records_warning > 0 && (
            <>
              {' · '}
              <Count value={b.records_warning} tone="warn" /> warn
            </>
          )}
          {b.records_error > 0 && (
            <>
              {' · '}
              <Count value={b.records_error} tone="crit" /> err
            </>
          )}
          {b.records_blocked > 0 && (
            <>
              {' · '}
              <Count value={b.records_blocked} tone="crit" /> blk
            </>
          )}
        </span>
      ),
    },
    {
      key: 'error',
      header: 'Error',
      render: (b) =>
        b.error_code ? (
          <Chip tone="crit" mono title={b.error_message ?? b.error_code}>
            {b.error_code}
          </Chip>
        ) : (
          <span className="text-slate-light">{DASH}</span>
        ),
    },
    {
      key: 'created',
      header: 'Created',
      align: 'right',
      sortable: true,
      sortAccessor: (b) => b.created_at,
      render: (b) => (
        <span className="whitespace-nowrap text-caption text-slate" title={fmtTs(b.created_at)}>
          {relTime(b.created_at)}
        </span>
      ),
    },
  ];

  return (
    <SectionCard
      title="Ingestion"
      subtitle={`${batches.length} recent batch(es) · open a row to diagnose`}
      noPadding
    >
      <DeepSectionBody
        loading={loading && !data}
        error={error}
        reload={reload}
        context="Loading ingestion"
        orgId={orgId}
        orgLabel={orgLabel}
        showEmpty={batches.length === 0}
        empty={
          <EmptyState
            title="No ingestion batches"
            description="Excel/CSV uploads, core-banking adapter runs, and API pushes appear here with their per-row outcomes."
          />
        }
      >
        <DataTable
          columns={columns}
          rows={batches}
          density="compact"
          initialSort={{ key: 'created', dir: 'desc' }}
          getFilterText={(b) =>
            [b.source_system, b.extraction_mode, b.status, b.error_code ?? '', b.as_of_date].join(' ')
          }
          filterPlaceholder="Filter batches…"
          pageSize={12}
          emptyMessage="No batches match this filter."
          onRowClick={(b) => setSelected(b.batch_id)}
        />
      </DeepSectionBody>

      <BatchDetailDrawer
        orgId={orgId}
        orgLabel={orgLabel}
        batchId={selected}
        onClose={() => setSelected(null)}
      />
    </SectionCard>
  );
}
