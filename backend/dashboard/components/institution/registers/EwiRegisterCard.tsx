'use client';

/**
 * EWI trigger-level register editor (LRMD ¶28(e)–(f)).
 *
 * The register behind the CFP page's early-warning dashboard: watch and
 * action trigger levels per indicator are Board configuration with approval
 * evidence. The read path is the period dashboard (values and RAG states are
 * computed server-side); the eight directive starter indicators are mirrored
 * here so a disabled starter stays visible and re-enableable — the dashboard
 * read omits disabled rows. Saves ride the audited, approver-gated register
 * PUT with the Board evidence (approved-by + required reason); only rows the
 * approver actually changed are included, and an included row carries its
 * full state because the register write replaces the row.
 */

import { useMemo, useState } from 'react';
import type {
  EwiDashboardRead,
  EwiIndicatorUpdate,
  EwiRegisterPut,
} from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { SkeletonTable } from '@/components/ui/Skeleton';
import { useEwiDashboard, useUpdateLiquidityEwiRegister } from '@/lib/api/hooks';
import { Field, FormActions, ReasonField, inputCls } from '@/components/institution/shared';
import { EditRegisterAction, numericInputCls, sameDecimal, useApproverGate } from './common';

// ---------------------------------------------------------------------------
// Directive starter vocabulary — mirrors the backend's LRMD ¶28(f) starter
// list so disabled starters (omitted by the dashboard read) stay visible.
// ---------------------------------------------------------------------------

type StarterMirror = {
  code: string;
  name: string;
  unit: string;
  direction: 'above' | 'below';
};

const STARTER_INDICATORS: StarterMirror[] = [
  {
    code: 'asset_growth_volatile_funding',
    name: 'Rapid asset growth funded by volatile liabilities',
    unit: 'pct',
    direction: 'above',
  },
  {
    code: 'funding_concentration',
    name: 'Growing concentration in funding sources',
    unit: 'pct',
    direction: 'above',
  },
  {
    code: 'currency_mismatch',
    name: 'Increase in currency mismatches',
    unit: 'pct',
    direction: 'above',
  },
  {
    code: 'weighted_liability_maturity',
    name: 'Decline in weighted-average maturity of liabilities',
    unit: 'days',
    direction: 'below',
  },
  {
    code: 'near_limit_incidents',
    name: 'Repeated incidents approaching internal or regulatory limits',
    unit: 'count',
    direction: 'above',
  },
  {
    code: 'earnings_asset_quality',
    name: 'Deterioration in earnings or asset quality',
    unit: 'pct',
    direction: 'above',
  },
  {
    code: 'debt_spreads',
    name: 'Widening of debt or credit-default spreads',
    unit: 'pct',
    direction: 'above',
  },
  {
    code: 'funding_costs',
    name: 'Rising cost of funding',
    unit: 'pct',
    direction: 'above',
  },
];

const STATUS_TONE: Record<string, StatusTone> = {
  normal: 'success',
  watch: 'amber',
  action: 'critical',
  unconfigured: 'slate',
  no_data: 'slate',
};

const STATUS_LABEL: Record<string, string> = {
  normal: 'Normal',
  watch: 'Watch',
  action: 'Action',
  unconfigured: 'Unconfigured',
  no_data: 'No data',
};

/** One row of the register editor — dashboard state merged over the mirror. */
type EwiRow = {
  code: string;
  name: string;
  metricBasis: string | null;
  unit: string;
  direction: 'above' | 'below';
  custom: boolean;
  enabled: boolean;
  watch: string;
  action: string;
  description: string | null;
  recoveryPlanReference: string | null;
  status: string | null;
};

function thresholdText(row: EwiRow, level: string): string {
  if (!level) return 'Not set';
  const op = row.direction === 'below' ? '<' : '≥';
  const unit = row.unit === 'count' ? '' : row.unit === 'days' ? ' days' : '%';
  return `${op} ${level}${unit}`;
}

function buildRows(dashboard: EwiDashboardRead): EwiRow[] {
  const byCode = new Map(dashboard.indicators.map((entry) => [entry.code, entry]));
  const rows: EwiRow[] = [];
  for (const starter of STARTER_INDICATORS) {
    const live = byCode.get(starter.code);
    rows.push(
      live
        ? {
            code: live.code,
            name: live.name,
            metricBasis: live.metricBasis,
            unit: String(live.unit),
            direction: live.direction === 'below' ? 'below' : 'above',
            custom: false,
            enabled: true,
            watch: live.watchThreshold == null ? '' : String(live.watchThreshold),
            action: live.actionThreshold == null ? '' : String(live.actionThreshold),
            description: live.description == null ? null : String(live.description),
            recoveryPlanReference:
              live.recoveryPlanReference == null ? null : String(live.recoveryPlanReference),
            status: String(live.status),
          }
        : {
            code: starter.code,
            name: starter.name,
            metricBasis: null,
            unit: starter.unit,
            direction: starter.direction,
            custom: false,
            enabled: false,
            watch: '',
            action: '',
            description: null,
            recoveryPlanReference: null,
            status: null,
          }
    );
  }
  // Board additions beyond the directive starters.
  for (const entry of dashboard.indicators) {
    if (STARTER_INDICATORS.some((starter) => starter.code === entry.code)) continue;
    rows.push({
      code: entry.code,
      name: entry.name,
      metricBasis: entry.metricBasis,
      unit: String(entry.unit),
      direction: entry.direction === 'below' ? 'below' : 'above',
      custom: true,
      enabled: true,
      watch: entry.watchThreshold == null ? '' : String(entry.watchThreshold),
      action: entry.actionThreshold == null ? '' : String(entry.actionThreshold),
      description: entry.description == null ? null : String(entry.description),
      recoveryPlanReference:
        entry.recoveryPlanReference == null ? null : String(entry.recoveryPlanReference),
      status: String(entry.status),
    });
  }
  return rows;
}

const viewColumns: Column<EwiRow>[] = [
  {
    key: 'name',
    header: 'Early-warning indicator',
    width: '36%',
    render: (r) => (
      <div>
        <p className="font-medium text-navy">{r.name}</p>
        <p className="text-caption text-slate">
          {r.metricBasis ?? 'Directive starter indicator'}
          {r.custom ? ' · Board addition' : ''}
        </p>
        {r.recoveryPlanReference ? (
          <p className="text-caption text-slate/80">
            Recovery plan: {r.recoveryPlanReference}
          </p>
        ) : null}
      </div>
    ),
  },
  {
    key: 'watch',
    header: 'Watch trigger',
    numeric: true,
    render: (r) => thresholdText(r, r.watch),
  },
  {
    key: 'action',
    header: 'Action trigger',
    numeric: true,
    render: (r) => thresholdText(r, r.action),
  },
  {
    key: 'status',
    header: 'Status',
    align: 'right',
    render: (r) =>
      r.enabled ? (
        <StatusPill tone={STATUS_TONE[r.status ?? ''] ?? 'slate'}>
          {STATUS_LABEL[r.status ?? ''] ?? r.status ?? '—'}
        </StatusPill>
      ) : (
        <StatusPill tone="slate">Disabled</StatusPill>
      ),
  },
];

export default function EwiRegisterCard({
  bankId,
  periodId,
}: {
  bankId: string;
  periodId: string | undefined;
}) {
  const { isApprover } = useApproverGate();
  const query = useEwiDashboard(bankId, periodId);
  const [editing, setEditing] = useState(false);

  const rows = useMemo(() => (query.data ? buildRows(query.data) : []), [query.data]);

  return (
    <SectionCard
      title="EWI trigger-level register"
      subtitle="Watch and action trigger levels for the early-warning indicators — Board configuration with approval evidence (LRMD ¶28(e)–(f))"
      noPadding
      actions={
        query.data && (
          <EditRegisterAction
            isApprover={isApprover}
            editing={editing}
            onEdit={() => setEditing(true)}
            label="Edit trigger levels"
          />
        )
      }
      footer={
        <span>
          Indicator values and RAG states are computed server-side on the CFP
          dashboard; an indicator without levels shows Unconfigured rather than
          an invented classification. Updates are approver-gated and audited.
        </span>
      }
    >
      {!periodId ? (
        <p className="px-5 py-6 text-body text-slate">
          Trigger levels are read through the reporting-period dashboard, and no
          reporting period exists yet — ingest data through the Data Engine
          first.
        </p>
      ) : query.isLoading ? (
        <SkeletonTable rows={5} />
      ) : query.error ? (
        <div className="p-5">
          <ErrorPanel
            error={query.error}
            title="Could not load the EWI register"
            onRetry={() => query.refetch()}
          />
        </div>
      ) : query.data ? (
        editing ? (
          <EwiEditor bankId={bankId} rows={rows} onClose={() => setEditing(false)} />
        ) : (
          <DataTable columns={viewColumns} rows={rows} density="compact" />
        )
      ) : null}
    </SectionCard>
  );
}

type EwiDraft = { watch: string; action: string; enabled: boolean };

function EwiEditor({
  bankId,
  rows,
  onClose,
}: {
  bankId: string;
  rows: EwiRow[];
  onClose: () => void;
}) {
  const update = useUpdateLiquidityEwiRegister(bankId);

  const [drafts, setDrafts] = useState<Record<string, EwiDraft>>(() => {
    const initial: Record<string, EwiDraft> = {};
    for (const row of rows) {
      initial[row.code] = { watch: row.watch, action: row.action, enabled: row.enabled };
    }
    return initial;
  });
  const [approvedBy, setApprovedBy] = useState('');
  const [reason, setReason] = useState('');

  const setDraft = (code: string, patch: Partial<EwiDraft>) =>
    setDrafts((prev) => ({ ...prev, [code]: { ...prev[code], ...patch } }));

  // Only rows the approver actually changed ride the PUT; each included row
  // carries its full state because the register write replaces the row.
  const changedEntries = useMemo(() => {
    const entries: EwiIndicatorUpdate[] = [];
    for (const row of rows) {
      const draft = drafts[row.code];
      if (!draft) continue;
      const watchChanged = !sameDecimal(draft.watch, row.watch || null);
      const actionChanged = !sameDecimal(draft.action, row.action || null);
      const enabledChanged = draft.enabled !== row.enabled;
      if (!watchChanged && !actionChanged && !enabledChanged) continue;
      entries.push({
        code: row.code,
        watchThreshold: draft.watch.trim() === '' ? null : draft.watch.trim(),
        actionThreshold: draft.action.trim() === '' ? null : draft.action.trim(),
        enabled: draft.enabled,
        custom: row.custom,
        recoveryPlanReference: row.recoveryPlanReference,
        // Custom indicators must carry their own display and semantics;
        // starters resolve these from the directive defaults.
        ...(row.custom
          ? {
              name: row.name,
              description: row.description,
              direction: row.direction,
              unit: row.unit as EwiIndicatorUpdate['unit'],
            }
          : {}),
      });
    }
    return entries;
  }, [rows, drafts]);

  const canSubmit =
    changedEntries.length > 0 && approvedBy.trim().length > 0 && reason.trim().length > 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const payload: EwiRegisterPut = {
      indicators: changedEntries,
      approvedBy: approvedBy.trim(),
      reason: reason.trim(),
    };
    update.mutate(payload, { onSuccess: onClose });
  };

  return (
    <form onSubmit={submit} className="px-5 pt-5 pb-5 space-y-4">
      <p className="text-micro font-medium text-slate uppercase tracking-wider">
        Record Board-approved trigger levels
      </p>

      <div className="space-y-2">
        {rows.map((row) => {
          const draft = drafts[row.code];
          if (!draft) return null;
          const unitSuffix =
            row.unit === 'count' ? 'count' : row.unit === 'days' ? 'days' : '%';
          return (
            <div
              key={row.code}
              className="flex flex-wrap items-center justify-between gap-3 rounded border border-border-light px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <p className="text-body font-medium text-navy">{row.name}</p>
                <p className="text-caption text-slate">
                  <span className="font-mono">{row.code}</span> · triggers{' '}
                  {row.direction === 'below' ? 'below' : 'at or above'} the level ·{' '}
                  {unitSuffix}
                  {row.custom ? ' · Board addition' : ''}
                </p>
              </div>
              <div className="shrink-0 flex items-center gap-3">
                <label
                  htmlFor={`ewi-${row.code}-watch`}
                  className="text-caption text-slate"
                >
                  Watch
                </label>
                <input
                  id={`ewi-${row.code}-watch`}
                  inputMode="decimal"
                  value={draft.watch}
                  onChange={(e) => setDraft(row.code, { watch: e.target.value })}
                  className={`${numericInputCls} w-24`}
                  disabled={!draft.enabled}
                />
                <label
                  htmlFor={`ewi-${row.code}-action`}
                  className="text-caption text-slate"
                >
                  Action
                </label>
                <input
                  id={`ewi-${row.code}-action`}
                  inputMode="decimal"
                  value={draft.action}
                  onChange={(e) => setDraft(row.code, { action: e.target.value })}
                  className={`${numericInputCls} w-24`}
                  disabled={!draft.enabled}
                />
                <label className="inline-flex items-center gap-1.5 text-caption text-slate">
                  <input
                    type="checkbox"
                    checked={draft.enabled}
                    onChange={(e) => setDraft(row.code, { enabled: e.target.checked })}
                  />
                  Enabled
                </label>
              </div>
            </div>
          );
        })}
      </div>

      <p className="text-caption text-slate">
        Re-enabling an indicator with blank triggers leaves it Unconfigured until
        the Board sets levels. Blank levels clear the stored trigger.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field
          label="Approved by"
          htmlFor="ewi-approved-by"
          required
          hint="Board minute reference or approving officer — recorded on every indicator row."
        >
          <input
            id="ewi-approved-by"
            value={approvedBy}
            onChange={(e) => setApprovedBy(e.target.value)}
            placeholder="e.g. Board minute BR-2026-014"
            className={inputCls}
          />
        </Field>
      </div>

      <div className="max-w-xl">
        <ReasonField id="ewi-reason" value={reason} onChange={setReason} />
      </div>

      <p className="text-caption text-slate">
        {changedEntries.length === 0
          ? 'No trigger levels changed yet — only changed indicators are written.'
          : `${changedEntries.length} indicator${changedEntries.length === 1 ? '' : 's'} will be updated.`}
      </p>

      {update.error && (
        <ErrorPanel error={update.error} title="Could not record the trigger levels" />
      )}

      <FormActions
        submitLabel="Record trigger levels"
        pending={update.isPending}
        disabled={!canSubmit}
        onCancel={onClose}
      />
    </form>
  );
}
