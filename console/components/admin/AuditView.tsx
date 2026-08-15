'use client';

import { useState } from 'react';
import { ChevronLeft, ChevronRight, Download, RotateCcw, Search } from 'lucide-react';
import { getAudit, type AuditLogItem } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { DASH, fmtTimestamp, fmtTs } from '@/lib/format';
import {
  Button,
  Chip,
  type Column,
  DataTable,
  Drawer,
  Field,
  FieldRow,
  InfoTip,
  Input,
  MonoId,
  PageHeader,
  SectionCard,
  Select,
  SkeletonRows,
} from '@/components/ui';
import { AdminBoundary } from './AdminBoundary';
import { downloadCsv, prettyDetail, toCsv } from './util';

interface Filters {
  operatorEmail: string;
  targetOrg: string;
  action: string;
  from: string;
  to: string;
}

const BLANK_FILTERS: Filters = {
  operatorEmail: '',
  targetOrg: '',
  action: '',
  from: '',
  to: '',
};

const LIMIT_OPTIONS = [25, 50, 100, 200];

export default function AuditView() {
  const [draft, setDraft] = useState<Filters>(BLANK_FILTERS);
  const [applied, setApplied] = useState<Filters>(BLANK_FILTERS);
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<AuditLogItem | null>(null);

  const { data, error, loading, reload } = useApi(
    () =>
      getAudit({
        operatorEmail: applied.operatorEmail || undefined,
        targetOrg: applied.targetOrg || undefined,
        action: applied.action || undefined,
        from: applied.from || undefined,
        to: applied.to || undefined,
        limit,
        offset,
      }),
    [
      applied.operatorEmail,
      applied.targetOrg,
      applied.action,
      applied.from,
      applied.to,
      limit,
      offset,
    ],
  );

  function apply() {
    setApplied({ ...draft });
    setOffset(0);
  }

  function resetFilters() {
    setDraft(BLANK_FILTERS);
    setApplied(BLANK_FILTERS);
    setOffset(0);
  }

  function exportCsv() {
    const items = data?.items ?? [];
    if (items.length === 0) return;
    const csv = toCsv(
      ['id', 'created_at', 'operator_email', 'auth_mode', 'action', 'target_org', 'detail'],
      items.map((i) => [
        i.id,
        i.created_at,
        i.operator_email,
        i.auth_mode,
        i.action,
        i.target_org ?? '',
        i.detail ?? '',
      ]),
    );
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    downloadCsv(`operator-audit-${stamp}.csv`, csv);
  }

  const total = data?.total ?? 0;
  const shown = data?.items.length ?? 0;
  const rangeLabel =
    total > 0 ? `${offset + 1}–${offset + shown} of ${total}` : loading ? 'Loading…' : 'No entries';
  const canPrev = offset > 0;
  const canNext = offset + limit < total;
  const hasFilters = Object.values(applied).some(Boolean);

  const columns: Column<AuditLogItem>[] = [
    {
      key: 'ts',
      header: 'Timestamp (UTC)',
      sortable: true,
      sortAccessor: (i) => i.created_at,
      render: (i) => <span className="font-mono text-caption text-navy">{fmtTimestamp(i.created_at)}</span>,
    },
    {
      key: 'action',
      header: 'Action',
      sortable: true,
      sortAccessor: (i) => i.action,
      render: (i) => <span className="font-mono text-caption text-ink">{i.action}</span>,
    },
    {
      key: 'operator',
      header: 'Operator',
      sortable: true,
      sortAccessor: (i) => i.operator_email,
      render: (i) => <span className="text-ink">{i.operator_email}</span>,
    },
    {
      key: 'target',
      header: 'Target org',
      render: (i) =>
        i.target_org ? <MonoId id={i.target_org} /> : <span className="text-slate-light">{DASH}</span>,
    },
    {
      key: 'auth',
      header: 'Auth',
      render: (i) => <Chip mono>{i.auth_mode}</Chip>,
    },
  ];

  return (
    <div>
      <PageHeader
        breadcrumbs={[{ label: 'Admin' }, { label: 'Audit Log' }]}
        title="Audit Log"
        subtitle="The append-only operator_audit_log — every staff mutation, filterable."
      />

      <div className="space-y-4">
        <SectionCard
          title="Filters"
          subtitle="Answer questions like: has any staff accessed tenant X in the last 90 days?"
        >
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Operator email" htmlFor="f-email">
              <Input
                id="f-email"
                placeholder="analyst@aequoros.com"
                value={draft.operatorEmail}
                onChange={(e) => setDraft((d) => ({ ...d, operatorEmail: e.target.value }))}
              />
            </Field>
            <Field label="Target org" htmlFor="f-org">
              <Input
                id="f-org"
                placeholder="OR-XXXXXXXX"
                value={draft.targetOrg}
                onChange={(e) => setDraft((d) => ({ ...d, targetOrg: e.target.value }))}
              />
            </Field>
            <Field label="Action prefix" htmlFor="f-action">
              <Input
                id="f-action"
                placeholder="operator. / inspector. / tenant."
                value={draft.action}
                onChange={(e) => setDraft((d) => ({ ...d, action: e.target.value }))}
              />
            </Field>
            <Field label="From" htmlFor="f-from">
              <Input
                id="f-from"
                type="date"
                value={draft.from}
                onChange={(e) => setDraft((d) => ({ ...d, from: e.target.value }))}
              />
            </Field>
            <Field label="To" htmlFor="f-to">
              <Input
                id="f-to"
                type="date"
                value={draft.to}
                onChange={(e) => setDraft((d) => ({ ...d, to: e.target.value }))}
              />
            </Field>
            <div className="flex items-end gap-2">
              <Button icon={<Search size={14} aria-hidden />} onClick={apply}>
                Apply
              </Button>
              <Button
                variant="secondary"
                icon={<RotateCcw size={14} aria-hidden />}
                onClick={resetFilters}
                disabled={!hasFilters && Object.values(draft).every((v) => !v)}
              >
                Reset
              </Button>
            </div>
          </div>
        </SectionCard>

        <SectionCard
          title={
            <span className="inline-flex items-center gap-1.5">
              Operator audit log
              <InfoTip label="About the audit log" width="w-80">
                This is the append-only OPERATOR action log — immutable by a database trigger
                (UPDATE and DELETE are blocked). Every operator mutation across all tenants lands
                here with the acting operator, auth mode, and target org.
              </InfoTip>
            </span>
          }
          subtitle={rangeLabel}
          actions={
            <>
              <div className="w-32">
                <Select
                  aria-label="Rows per page"
                  value={String(limit)}
                  onChange={(e) => {
                    setLimit(Number(e.target.value));
                    setOffset(0);
                  }}
                >
                  {LIMIT_OPTIONS.map((n) => (
                    <option key={n} value={n}>
                      {n} / page
                    </option>
                  ))}
                </Select>
              </div>
              <Button
                variant="secondary"
                icon={<Download size={14} aria-hidden />}
                onClick={exportCsv}
                disabled={shown === 0}
                title="Download the loaded rows as CSV"
              >
                Export CSV
              </Button>
            </>
          }
          noPadding
          footer={
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                icon={<ChevronLeft size={13} aria-hidden />}
                onClick={() => setOffset((o) => Math.max(0, o - limit))}
                disabled={!canPrev || loading}
              >
                Prev
              </Button>
              <Button
                size="sm"
                variant="secondary"
                icon={<ChevronRight size={13} aria-hidden />}
                onClick={() => setOffset((o) => o + limit)}
                disabled={!canNext || loading}
              >
                Next
              </Button>
              <span className="text-caption text-slate tnum">{rangeLabel}</span>
            </div>
          }
        >
          <AdminBoundary
            loading={loading}
            error={error}
            onRetry={reload}
            surface="Audit log"
            skeleton={<SkeletonRows rows={8} />}
          >
            <DataTable
              columns={columns}
              rows={data?.items ?? []}
              onRowClick={(row) => setSelected(row)}
              initialSort={{ key: 'ts', dir: 'desc' }}
              emptyMessage={
                hasFilters ? 'No audit entries match these filters.' : 'No audit entries yet.'
              }
            />
          </AdminBoundary>
        </SectionCard>
      </div>

      <Drawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        title="Audit entry"
        description={selected?.action}
        width="w-[560px]"
      >
        {selected && (
          <div className="space-y-4">
            <div className="rounded-md border border-border-light bg-surface px-4 py-2">
              <FieldRow label="Entry ID">
                <MonoId id={selected.id} />
              </FieldRow>
              <FieldRow label="When">
                <span className="font-mono text-caption" title={fmtTs(selected.created_at)}>
                  {fmtTimestamp(selected.created_at)}
                </span>
              </FieldRow>
              <FieldRow label="Operator">{selected.operator_email}</FieldRow>
              <FieldRow label="Auth mode">
                <Chip mono>{selected.auth_mode}</Chip>
              </FieldRow>
              <FieldRow label="Action">
                <span className="font-mono text-caption">{selected.action}</span>
              </FieldRow>
              <FieldRow label="Target org">
                {selected.target_org ? (
                  <MonoId id={selected.target_org} />
                ) : (
                  <span className="text-slate-light">{DASH}</span>
                )}
              </FieldRow>
            </div>

            <div>
              <p className="mb-1.5 text-caption font-medium text-slate">Detail</p>
              {selected.detail ? (
                <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap break-words rounded-md border border-border-light bg-surface p-3 font-mono text-caption text-ink">
                  {prettyDetail(selected.detail)}
                </pre>
              ) : (
                <p className="text-caption text-slate-light">{DASH}</p>
              )}
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}
