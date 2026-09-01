'use client';

/**
 * Board credit early-warning threshold register (credit module).
 *
 * The four watch levels the credit engine compares against on every
 * computation — NPL Board trigger, provision-coverage floor, employer PAR30
 * early warning, restructured-ratio watch. They start EMPTY on purpose: no
 * regulatory instrument prescribes the values, so nothing is defaulted and an
 * unset level is disclosed as "not evaluated" rather than silently skipped.
 * Each save records an effective-dated generation per changed code, carrying
 * the Board evidence; breaches surface as credit validations and alerts.
 */

import { useMemo, useState } from 'react';
import type {
  CreditThresholdRegisterRead,
  CreditThresholdUpdate,
} from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { SkeletonTable } from '@/components/ui/Skeleton';
import {
  useCreditThresholdRegister,
  useUpdateCreditThresholdRegister,
} from '@/lib/api/hooks';
import { fmtDateUTC, num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import { FormActions, ReasonField } from '@/components/institution/shared';
import {
  EditRegisterAction,
  EvidenceFields,
  numericInputCls,
  parseDecimalInput,
  sameDecimal,
  useApproverGate,
} from './common';

/** The closed code list (mirrors the backend's CREDIT_THRESHOLD_CODES). */
const THRESHOLDS: { code: string; label: string; hint: string }[] = [
  {
    code: 'npl_board_trigger_pct',
    label: 'NPL Board trigger',
    hint: 'Early-warning NPL level below the regulatory ceiling',
  },
  {
    code: 'provision_coverage_floor_pct',
    label: 'Provision coverage floor',
    hint: 'Minimum specific-provisions-to-NPL coverage the Board expects',
  },
  {
    code: 'employer_par30_ewi_pct',
    label: 'Employer PAR30 early warning',
    hint: 'Per-employer PAR30 level for the payroll / check-off book',
  },
  {
    code: 'restructured_ratio_watch_pct',
    label: 'Restructured ratio watch',
    hint: 'Restructured share of the book worth Board attention',
  },
];

type ViewRow = {
  code: string;
  label: string;
  hint: string;
  valuePct: string | null;
  approvedBy: string | null;
  effectiveFrom: string | null;
};

const viewColumns: Column<ViewRow>[] = [
  {
    key: 'threshold',
    header: 'Threshold',
    width: '40%',
    render: (r) => (
      <div>
        <p className="text-body font-medium text-navy">{r.label}</p>
        <p className="text-caption text-slate">{r.hint}</p>
      </div>
    ),
  },
  {
    key: 'level',
    header: 'Level',
    numeric: true,
    render: (r) =>
      r.valuePct !== null ? (
        <span className="font-mono tnum text-navy">{fmtPct(num(r.valuePct), 2)}</span>
      ) : (
        <StatusPill tone="slate">Not set</StatusPill>
      ),
  },
  {
    key: 'evidence',
    header: 'Approval evidence',
    render: (r) =>
      r.approvedBy ? (
        <div>
          <p className="text-body text-navy">{r.approvedBy}</p>
          <p className="text-caption text-slate">
            Effective {r.effectiveFrom ? fmtDateUTC(new Date(r.effectiveFrom)) : '—'}
          </p>
        </div>
      ) : (
        <span className="text-caption text-slate">Not evaluated until set</span>
      ),
  },
];

function toViewRows(register: CreditThresholdRegisterRead): ViewRow[] {
  const byCode = new Map(register.thresholds.map((row) => [row.thresholdCode, row]));
  return THRESHOLDS.map(({ code, label, hint }) => {
    const row = byCode.get(code);
    return {
      code,
      label,
      hint,
      valuePct: row ? String(row.valuePct) : null,
      approvedBy: row?.approvedBy ?? null,
      effectiveFrom: row?.effectiveFrom ?? null,
    };
  });
}

export default function CreditThresholdCard({ bankId }: { bankId: string }) {
  const { isApprover } = useApproverGate();
  const query = useCreditThresholdRegister(bankId);
  const [editing, setEditing] = useState(false);

  return (
    <SectionCard
      title="Credit early-warning thresholds"
      subtitle="The Board's own watch levels for the credit module — evaluated on every computation once set, disclosed as not-evaluated until then"
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
          No regulatory instrument prescribes these values — they are the
          institution&apos;s own levels, distinct from the Notice 2025/23 NPL
          ceiling the engine always enforces. Breaches raise credit validations
          and alerts; updates are approver-gated and audited.
        </span>
      }
    >
      {query.isLoading ? (
        <SkeletonTable rows={4} />
      ) : query.error ? (
        <div className="p-5">
          <ErrorPanel
            error={query.error}
            title="Could not load the credit threshold register"
            onRetry={() => query.refetch()}
          />
        </div>
      ) : query.data ? (
        editing ? (
          <ThresholdEditor
            bankId={bankId}
            register={query.data}
            onClose={() => setEditing(false)}
          />
        ) : (
          <DataTable columns={viewColumns} rows={toViewRows(query.data)} density="compact" />
        )
      ) : null}
    </SectionCard>
  );
}

function ThresholdEditor({
  bankId,
  register,
  onClose,
}: {
  bankId: string;
  register: CreditThresholdRegisterRead;
  onClose: () => void;
}) {
  const update = useUpdateCreditThresholdRegister(bankId);

  const currentByCode = useMemo(
    () =>
      new Map(
        register.thresholds.map((row) => [row.thresholdCode, String(row.valuePct)])
      ),
    [register]
  );
  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const { code } of THRESHOLDS) {
      initial[code] = currentByCode.get(code) ?? '';
    }
    return initial;
  });
  const [effectiveFrom, setEffectiveFrom] = useState('');
  const [approvedBy, setApprovedBy] = useState('');
  const [reason, setReason] = useState('');

  const { changed, invalid } = useMemo(() => {
    const changedCodes: Record<string, string> = {};
    const invalidCodes: string[] = [];
    for (const { code } of THRESHOLDS) {
      const input = values[code] ?? '';
      // Blank leaves an unset code unset and a set code unchanged — the
      // register has no "unset" operation; a generation supersedes per code.
      if (input.trim().length === 0) continue;
      if (sameDecimal(input, currentByCode.get(code) ?? null)) continue;
      const parsed = parseDecimalInput(input);
      if (parsed === null || parsed < 0) {
        invalidCodes.push(code);
        continue;
      }
      changedCodes[code] = input.trim();
    }
    return { changed: changedCodes, invalid: invalidCodes };
  }, [values, currentByCode]);

  const changedCount = Object.keys(changed).length;
  const canSubmit =
    changedCount > 0 &&
    invalid.length === 0 &&
    effectiveFrom.trim().length > 0 &&
    approvedBy.trim().length > 0 &&
    reason.trim().length > 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const payload: CreditThresholdUpdate = {
      thresholds: changed,
      effectiveFrom: new Date(effectiveFrom),
      approvedBy: approvedBy.trim(),
      reason: reason.trim(),
    };
    update.mutate(payload, { onSuccess: onClose });
  };

  return (
    <form onSubmit={submit} className="px-5 pt-5 pb-5 space-y-4">
      <p className="text-micro font-medium text-slate uppercase tracking-wider">
        Record Board-approved watch levels
      </p>

      <div className="space-y-2">
        {THRESHOLDS.map(({ code, label, hint }) => (
          <div
            key={code}
            className="flex items-center justify-between gap-4 rounded border border-border-light px-3 py-2"
          >
            <div className="min-w-0">
              <label htmlFor={`cth-${code}`} className="block text-body font-medium text-navy">
                {label}
              </label>
              <p className="text-caption text-slate">
                {hint} ·{' '}
                {currentByCode.has(code)
                  ? `currently ${fmtPct(num(currentByCode.get(code)!), 2)}`
                  : 'not set — leave blank to keep it unevaluated'}
              </p>
            </div>
            <div className="shrink-0 flex items-center gap-1.5">
              <input
                id={`cth-${code}`}
                inputMode="decimal"
                value={values[code] ?? ''}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, [code]: e.target.value }))
                }
                className={`${numericInputCls} w-28 ${
                  invalid.includes(code) ? 'border-critical' : ''
                }`}
                aria-invalid={invalid.includes(code)}
              />
              <span className="text-caption text-slate">%</span>
            </div>
          </div>
        ))}
      </div>

      <EvidenceFields
        idPrefix="cth"
        effectiveFrom={effectiveFrom}
        onEffectiveFrom={setEffectiveFrom}
        approvedBy={approvedBy}
        onApprovedBy={setApprovedBy}
      />

      <div className="max-w-xl">
        <ReasonField id="cth-reason" value={reason} onChange={setReason} />
      </div>

      <p className="text-caption text-slate">
        {changedCount === 0
          ? 'No levels changed yet — only changed thresholds are recorded.'
          : `${changedCount} threshold${changedCount === 1 ? '' : 's'} will be recorded in this generation.`}
      </p>

      {update.error && (
        <ErrorPanel error={update.error} title="Could not record the threshold generation" />
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
