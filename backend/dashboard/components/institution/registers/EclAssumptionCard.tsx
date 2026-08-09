'use client';

/**
 * IFRS 9 ECL assumptions register editor (PD/LGD per segment and stage).
 *
 * The ECL engine activates only when `ecl_exposure` facts AND this register
 * exist — until then the ingested-provisions path is byte-identical, so an
 * empty register is a deliberate state, not a gap. Edit mode records a new
 * effective-dated generation for changed rows and lets the Board adopt new
 * segment/stage assumptions; every save carries the approval evidence and
 * the required audit reason. Approver-gated server-side.
 */

import { useMemo, useState } from 'react';
import { BookOpenCheck, Plus, Trash2 } from 'lucide-react';
import type {
  EclAssumptionEntry,
  EclAssumptionRead,
  EclAssumptionRegisterRead,
  EclAssumptionUpdate,
} from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import DataTable, { type Column } from '@/components/ui/DataTable';
import EmptyState from '@/components/ui/EmptyState';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { SkeletonTable } from '@/components/ui/Skeleton';
import {
  useEclAssumptionRegister,
  useUpdateEclAssumptionRegister,
} from '@/lib/api/hooks';
import { fmtDateUTC, num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import {
  FormActions,
  ReasonField,
  inputCls,
  textOrNull,
} from '@/components/institution/shared';
import {
  EditRegisterAction,
  EvidenceFields,
  numericInputCls,
  parseDecimalInput,
  sameDecimal,
  useApproverGate,
} from './common';

const viewColumns: Column<EclAssumptionRead>[] = [
  {
    key: 'segment',
    header: 'Segment',
    width: '28%',
    render: (r) => <span className="font-mono text-body text-navy">{r.segment}</span>,
  },
  {
    key: 'stage',
    header: 'Stage',
    align: 'center',
    render: (r) => <span className="font-mono tnum">{r.stage}</span>,
  },
  {
    key: 'pd',
    header: 'PD',
    numeric: true,
    render: (r) => fmtPct(num(r.pdPct), 2),
  },
  {
    key: 'lgd',
    header: 'LGD',
    numeric: true,
    render: (r) => fmtPct(num(r.lgdPct), 2),
  },
  {
    key: 'evidence',
    header: 'Approval evidence',
    render: (r) => (
      <div>
        <p className="text-body text-navy">{r.approvedBy}</p>
        <p className="text-caption text-slate">Effective {fmtDateUTC(r.effectiveFrom)}</p>
      </div>
    ),
  },
];

export default function EclAssumptionCard({ bankId }: { bankId: string }) {
  const { isApprover } = useApproverGate();
  const query = useEclAssumptionRegister(bankId);
  const [editing, setEditing] = useState(false);

  return (
    <SectionCard
      title="ECL assumptions register"
      subtitle="IFRS 9 expected-credit-loss assumptions — Board-adopted PD and LGD per segment and stage"
      noPadding
      actions={
        query.data && (
          <EditRegisterAction
            isApprover={isApprover}
            editing={editing}
            onEdit={() => setEditing(true)}
          />
        )
      }
      footer={
        <span>
          The ECL engine activates only when exposure facts and this register
          both exist — until then provisions flow from ingested figures
          unchanged. Updates are approver-gated and audited;
          {` ${query.data?.history.length ?? 0} generation${(query.data?.history.length ?? 0) === 1 ? '' : 's'} on record.`}
        </span>
      }
    >
      {query.isLoading ? (
        <SkeletonTable rows={4} />
      ) : query.error ? (
        <div className="p-5">
          <ErrorPanel
            error={query.error}
            title="Could not load the ECL assumptions register"
            onRetry={() => query.refetch()}
          />
        </div>
      ) : query.data ? (
        editing ? (
          <EclEditor
            bankId={bankId}
            register={query.data}
            onClose={() => setEditing(false)}
          />
        ) : query.data.assumptions.length > 0 ? (
          <DataTable columns={viewColumns} rows={query.data.assumptions} density="compact" />
        ) : (
          <div className="p-5">
            <EmptyState
              Icon={BookOpenCheck}
              title="No assumptions adopted"
              description="ECL runs on ingested provisions until the Board adopts PD/LGD assumptions here — an empty register is a deliberate state, not a gap."
            />
          </div>
        )
      ) : null}
    </SectionCard>
  );
}

type EclDraft = { key: string; segment: string; stage: number; pd: string; lgd: string };
type NewRow = { segment: string; stage: string; pd: string; lgd: string };

const BLANK_NEW_ROW: NewRow = { segment: '', stage: '1', pd: '', lgd: '' };

function EclEditor({
  bankId,
  register,
  onClose,
}: {
  bankId: string;
  register: EclAssumptionRegisterRead;
  onClose: () => void;
}) {
  const update = useUpdateEclAssumptionRegister(bankId);

  const [drafts, setDrafts] = useState<EclDraft[]>(() =>
    register.assumptions.map((row) => ({
      key: `${row.segment}:${row.stage}`,
      segment: row.segment,
      stage: row.stage,
      pd: String(row.pdPct),
      lgd: String(row.lgdPct),
    }))
  );
  const [newRows, setNewRows] = useState<NewRow[]>(
    register.assumptions.length === 0 ? [{ ...BLANK_NEW_ROW }] : []
  );
  const [effectiveFrom, setEffectiveFrom] = useState('');
  const [approvedBy, setApprovedBy] = useState('');
  const [notes, setNotes] = useState('');
  const [reason, setReason] = useState('');

  const currentByKey = useMemo(
    () =>
      new Map(
        register.assumptions.map((row) => [
          `${row.segment}:${row.stage}`,
          { pd: String(row.pdPct), lgd: String(row.lgdPct) },
        ])
      ),
    [register]
  );

  const setDraft = (key: string, patch: Partial<EclDraft>) =>
    setDrafts((prev) =>
      prev.map((draft) => (draft.key === key ? { ...draft, ...patch } : draft))
    );

  const setNewRow = (index: number, patch: Partial<NewRow>) =>
    setNewRows((prev) =>
      prev.map((row, i) => (i === index ? { ...row, ...patch } : row))
    );

  const validPct = (value: string): boolean => {
    const parsed = parseDecimalInput(value);
    return parsed !== null && parsed >= 0 && parsed <= 100;
  };

  const { entries, problems } = useMemo(() => {
    const collected: EclAssumptionEntry[] = [];
    const issues: string[] = [];
    const seen = new Set<string>();

    for (const draft of drafts) {
      const current = currentByKey.get(draft.key);
      const changed =
        !current ||
        !sameDecimal(draft.pd, current.pd) ||
        !sameDecimal(draft.lgd, current.lgd);
      if (!changed) continue;
      if (!validPct(draft.pd) || !validPct(draft.lgd)) {
        issues.push(`${draft.segment} stage ${draft.stage}: PD and LGD must be 0–100.`);
        continue;
      }
      seen.add(draft.key);
      collected.push({
        segment: draft.segment,
        stage: draft.stage,
        pdPct: draft.pd.trim(),
        lgdPct: draft.lgd.trim(),
      });
    }

    for (const row of newRows) {
      const untouched = !row.segment.trim() && !row.pd.trim() && !row.lgd.trim();
      if (untouched) continue;
      const segment = row.segment.trim().toUpperCase();
      const stage = Number(row.stage);
      if (!segment) {
        issues.push('New assumption rows need a segment.');
        continue;
      }
      if (!validPct(row.pd) || !validPct(row.lgd)) {
        issues.push(`${segment} stage ${stage}: PD and LGD must be 0–100.`);
        continue;
      }
      const key = `${segment}:${stage}`;
      if (seen.has(key)) {
        issues.push(`${segment} stage ${stage} appears twice in this generation.`);
        continue;
      }
      seen.add(key);
      collected.push({ segment, stage, pdPct: row.pd.trim(), lgdPct: row.lgd.trim() });
    }

    return { entries: collected, problems: issues };
  }, [drafts, newRows, currentByKey]);

  const canSubmit =
    entries.length > 0 &&
    problems.length === 0 &&
    effectiveFrom.trim().length > 0 &&
    approvedBy.trim().length > 0 &&
    reason.trim().length > 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const payload: EclAssumptionUpdate = {
      assumptions: entries,
      effectiveFrom: new Date(effectiveFrom),
      approvedBy: approvedBy.trim(),
      reason: reason.trim(),
      notes: textOrNull(notes),
    };
    update.mutate(payload, { onSuccess: onClose });
  };

  return (
    <form onSubmit={submit} className="px-5 pt-5 pb-5 space-y-4">
      <p className="text-micro font-medium text-slate uppercase tracking-wider">
        Record a Board-approved assumptions generation
      </p>

      {drafts.length > 0 && (
        <div className="space-y-2">
          {drafts.map((draft) => (
            <div
              key={draft.key}
              className="flex flex-wrap items-center justify-between gap-3 rounded border border-border-light px-3 py-2"
            >
              <div className="min-w-0">
                <p className="text-body font-medium text-navy font-mono">
                  {draft.segment}
                </p>
                <p className="text-caption text-slate">Stage {draft.stage}</p>
              </div>
              <div className="shrink-0 flex items-center gap-3">
                <label
                  htmlFor={`ecl-${draft.key}-pd`}
                  className="text-caption text-slate"
                >
                  PD %
                </label>
                <input
                  id={`ecl-${draft.key}-pd`}
                  inputMode="decimal"
                  value={draft.pd}
                  onChange={(e) => setDraft(draft.key, { pd: e.target.value })}
                  className={`${numericInputCls} w-24`}
                />
                <label
                  htmlFor={`ecl-${draft.key}-lgd`}
                  className="text-caption text-slate"
                >
                  LGD %
                </label>
                <input
                  id={`ecl-${draft.key}-lgd`}
                  inputMode="decimal"
                  value={draft.lgd}
                  onChange={(e) => setDraft(draft.key, { lgd: e.target.value })}
                  className={`${numericInputCls} w-24`}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {newRows.length > 0 && (
        <div className="space-y-2">
          <p className="text-caption font-medium text-navy">New assumptions</p>
          {newRows.map((row, index) => (
            <div
              key={index}
              className="flex flex-wrap items-end gap-3 rounded border border-border-light px-3 py-2"
            >
              <div className="min-w-[10rem]">
                <label
                  htmlFor={`ecl-new-${index}-segment`}
                  className="block text-caption font-medium text-navy mb-1"
                >
                  Segment
                </label>
                <input
                  id={`ecl-new-${index}-segment`}
                  value={row.segment}
                  onChange={(e) => setNewRow(index, { segment: e.target.value })}
                  placeholder="e.g. CORPORATE"
                  className={`${inputCls} font-mono uppercase`}
                />
              </div>
              <div>
                <label
                  htmlFor={`ecl-new-${index}-stage`}
                  className="block text-caption font-medium text-navy mb-1"
                >
                  Stage
                </label>
                <select
                  id={`ecl-new-${index}-stage`}
                  value={row.stage}
                  onChange={(e) => setNewRow(index, { stage: e.target.value })}
                  className={inputCls}
                >
                  <option value="1">1</option>
                  <option value="2">2</option>
                  <option value="3">3</option>
                </select>
              </div>
              <div>
                <label
                  htmlFor={`ecl-new-${index}-pd`}
                  className="block text-caption font-medium text-navy mb-1"
                >
                  PD %
                </label>
                <input
                  id={`ecl-new-${index}-pd`}
                  inputMode="decimal"
                  value={row.pd}
                  onChange={(e) => setNewRow(index, { pd: e.target.value })}
                  className={`${numericInputCls} w-24`}
                />
              </div>
              <div>
                <label
                  htmlFor={`ecl-new-${index}-lgd`}
                  className="block text-caption font-medium text-navy mb-1"
                >
                  LGD %
                </label>
                <input
                  id={`ecl-new-${index}-lgd`}
                  inputMode="decimal"
                  value={row.lgd}
                  onChange={(e) => setNewRow(index, { lgd: e.target.value })}
                  className={`${numericInputCls} w-24`}
                />
              </div>
              <button
                type="button"
                onClick={() => setNewRows((prev) => prev.filter((_, i) => i !== index))}
                className="inline-flex items-center gap-1 rounded border border-border px-2 py-1.5 text-micro font-medium text-slate hover:text-navy hover:border-slate"
              >
                <Trash2 size={11} aria-hidden />
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      <button
        type="button"
        onClick={() => setNewRows((prev) => [...prev, { ...BLANK_NEW_ROW }])}
        className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-micro font-medium text-slate hover:text-navy hover:border-slate"
      >
        <Plus size={11} aria-hidden />
        Add segment/stage assumption
      </button>

      <EvidenceFields
        idPrefix="ecl"
        effectiveFrom={effectiveFrom}
        onEffectiveFrom={setEffectiveFrom}
        approvedBy={approvedBy}
        onApprovedBy={setApprovedBy}
        notes={notes}
        onNotes={setNotes}
      />

      <div className="max-w-xl">
        <ReasonField id="ecl-reason" value={reason} onChange={setReason} />
      </div>

      {problems.length > 0 && (
        <ul className="text-caption text-critical space-y-0.5">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}

      <p className="text-caption text-slate">
        {entries.length === 0
          ? 'No assumptions changed yet — only changed or newly adopted rows are recorded.'
          : `${entries.length} assumption${entries.length === 1 ? '' : 's'} will be recorded in this generation.`}
      </p>

      {update.error && (
        <ErrorPanel
          error={update.error}
          title="Could not record the assumptions generation"
        />
      )}

      <FormActions
        submitLabel="Record generation"
        pending={update.isPending}
        disabled={!canSubmit}
        onCancel={onClose}
      />
    </form>
  );
}
