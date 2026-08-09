'use client';

/**
 * Board threshold register editor (LMTD 2026 ¶11(b)–(e)).
 *
 * View mode is the same register the Liquidity → Monitoring Tools page reads:
 * the resolved thresholds — the Board's adopted levels over the directive's
 * published minimums — with the approval evidence per row. Edit mode records
 * a new Board-approved generation: only the codes whose level actually
 * changed ride the PUT (codes not present keep their current resolution),
 * and the write carries the Board evidence (effective-from, approved-by,
 * required audit reason). The PUT is approver-gated server-side; the Edit
 * action mirrors that gate.
 */

import { useMemo, useState } from 'react';
import type {
  LiquidityThresholdRegisterRead,
  LiquidityThresholdRead,
  LiquidityThresholdUpdate,
} from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { SkeletonTable } from '@/components/ui/Skeleton';
import {
  useLiquidityThresholdRegister,
  useUpdateLiquidityThresholdRegister,
} from '@/lib/api/hooks';
import { fmtDateUTC, num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import {
  FormActions,
  ReasonField,
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

/** LMTD Table 1 ratio vocabulary (the backend's threshold codes). */
const THRESHOLD_LABELS: Record<string, string> = {
  narrow_to_volatile: 'Narrow liquid assets / volatile funds',
  broad_to_volatile: 'Broad liquid assets / volatile funds',
  narrow_to_short_term: 'Narrow liquid assets / short-term liabilities',
  broad_to_short_term: 'Broad liquid assets / short-term liabilities',
  narrow_to_total_deposits: 'Narrow liquid assets / total deposits',
  broad_to_total_deposits: 'Broad liquid assets / total deposits',
  narrow_to_total_assets: 'Narrow liquid assets / total assets',
  broad_to_total_assets: 'Broad liquid assets / total assets',
  currency_mismatch_limit_pct: 'Currency mismatch limit',
};

/**
 * Limit-shaped codes the Board may adopt beyond the Table 1 ratio floors —
 * mirrors the backend's EXTRA_THRESHOLD_CODES: no directive default exists,
 * so absent a Board row no check runs (¶11(c)–(e) are Board obligations).
 */
const ADOPTABLE_EXTRA_CODES = ['currency_mismatch_limit_pct'];

function thresholdLabel(code: string): string {
  return THRESHOLD_LABELS[code] ?? code;
}

const viewColumns: Column<LiquidityThresholdRead>[] = [
  {
    key: 'code',
    header: 'Threshold',
    width: '34%',
    render: (r) => (
      <div>
        <p className="font-medium text-navy">{thresholdLabel(r.thresholdCode)}</p>
        <p className="text-caption text-slate font-mono">{r.thresholdCode}</p>
      </div>
    ),
  },
  {
    key: 'level',
    header: 'Active level',
    numeric: true,
    render: (r) => fmtPct(num(r.thresholdPct), 2),
  },
  {
    key: 'source',
    header: 'Source',
    render: (r) =>
      r.source === 'board_register' ? (
        <StatusPill tone="success">Board register</StatusPill>
      ) : (
        <StatusPill tone="slate">Directive minimum</StatusPill>
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
            Effective {r.effectiveFrom ? fmtDateUTC(r.effectiveFrom) : '—'}
          </p>
        </div>
      ) : (
        <span className="text-caption text-slate">
          Regulatory default — no Board row yet
        </span>
      ),
  },
];

export default function ThresholdRegisterCard({ bankId }: { bankId: string }) {
  const { isApprover } = useApproverGate();
  const query = useLiquidityThresholdRegister(bankId);
  const [editing, setEditing] = useState(false);

  return (
    <SectionCard
      title="Board threshold register"
      subtitle="Liquidity thresholds — the Board's adopted levels over the directive's published minimums (LMTD ¶11(b)–(e))"
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
          Each save records an effective-dated, Board-approved generation —
          {` ${query.data?.history.length ?? 0} on record. `}
          Updates are approver-gated and audited; the Monitoring Tools page
          reads this same register.
        </span>
      }
    >
      {query.isLoading ? (
        <SkeletonTable rows={5} />
      ) : query.error ? (
        <div className="p-5">
          <ErrorPanel
            error={query.error}
            title="Could not load the threshold register"
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
          <DataTable columns={viewColumns} rows={query.data.thresholds} density="compact" />
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
  register: LiquidityThresholdRegisterRead;
  onClose: () => void;
}) {
  const update = useUpdateLiquidityThresholdRegister(bankId);

  // Editable rows: the resolved bank-class thresholds, plus an adoption row
  // for each limit-shaped extra code the Board has not adopted yet.
  const rows = useMemo(() => {
    const bankRows = register.thresholds.filter((r) => r.institutionClass === 'bank');
    const present = new Set(bankRows.map((r) => r.thresholdCode));
    const extras = ADOPTABLE_EXTRA_CODES.filter((code) => !present.has(code));
    return { bankRows, extras };
  }, [register]);

  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const row of rows.bankRows) initial[row.thresholdCode] = String(row.thresholdPct);
    for (const code of rows.extras) initial[code] = '';
    return initial;
  });
  const [effectiveFrom, setEffectiveFrom] = useState('');
  const [approvedBy, setApprovedBy] = useState('');
  const [notes, setNotes] = useState('');
  const [reason, setReason] = useState('');

  const currentByCode = useMemo(
    () =>
      new Map(rows.bankRows.map((row) => [row.thresholdCode, String(row.thresholdPct)])),
    [rows]
  );

  // Changed-only payload; a filled input must be a positive number.
  const { changed, invalid } = useMemo(() => {
    const changedCodes: Record<string, string> = {};
    const invalidCodes: string[] = [];
    for (const [code, input] of Object.entries(values)) {
      const current = currentByCode.get(code);
      if (sameDecimal(input, current ?? null)) continue;
      if (input.trim() === '') continue; // an emptied extra row simply is not adopted
      const parsed = parseDecimalInput(input);
      if (parsed === null || parsed <= 0) {
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
    const payload: LiquidityThresholdUpdate = {
      institutionClass: 'bank',
      effectiveFrom: new Date(effectiveFrom),
      approvedBy: approvedBy.trim(),
      thresholds: changed,
      reason: reason.trim(),
      notes: textOrNull(notes),
    };
    update.mutate(payload, { onSuccess: onClose });
  };

  return (
    <form onSubmit={submit} className="px-5 pt-5 pb-5 space-y-4">
      <p className="text-micro font-medium text-slate uppercase tracking-wider">
        Record a Board-approved threshold generation
      </p>

      <div className="space-y-2">
        {rows.bankRows.map((row) => (
          <ThresholdEditRow
            key={row.thresholdCode}
            code={row.thresholdCode}
            hint={
              row.source === 'board_register'
                ? `Board level ${fmtPct(num(row.thresholdPct), 2)}`
                : `Directive minimum ${fmtPct(num(row.thresholdPct), 2)}`
            }
            value={values[row.thresholdCode] ?? ''}
            invalid={invalid.includes(row.thresholdCode)}
            onChange={(next) =>
              setValues((prev) => ({ ...prev, [row.thresholdCode]: next }))
            }
          />
        ))}
        {rows.extras.map((code) => (
          <ThresholdEditRow
            key={code}
            code={code}
            hint="No directive default — adopted only by a Board row; leave blank to keep it unadopted"
            value={values[code] ?? ''}
            invalid={invalid.includes(code)}
            onChange={(next) => setValues((prev) => ({ ...prev, [code]: next }))}
          />
        ))}
      </div>

      <EvidenceFields
        idPrefix="thr"
        effectiveFrom={effectiveFrom}
        onEffectiveFrom={setEffectiveFrom}
        approvedBy={approvedBy}
        onApprovedBy={setApprovedBy}
        notes={notes}
        onNotes={setNotes}
      />

      <div className="max-w-xl">
        <ReasonField id="thr-reason" value={reason} onChange={setReason} />
      </div>

      <p className="text-caption text-slate">
        {changedCount === 0
          ? 'No levels changed yet — only changed codes are recorded; the rest keep their current resolution.'
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

function ThresholdEditRow({
  code,
  hint,
  value,
  invalid,
  onChange,
}: {
  code: string;
  hint: string;
  value: string;
  invalid: boolean;
  onChange: (next: string) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded border border-border-light px-3 py-2">
      <div className="min-w-0">
        <label
          htmlFor={`thr-${code}`}
          className="block text-body font-medium text-navy"
        >
          {thresholdLabel(code)}
        </label>
        <p className="text-caption text-slate">
          <span className="font-mono">{code}</span> · {hint}
        </p>
      </div>
      <div className="shrink-0 flex items-center gap-1.5">
        <input
          id={`thr-${code}`}
          inputMode="decimal"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={`${numericInputCls} w-28 ${invalid ? 'border-critical' : ''}`}
          aria-invalid={invalid}
        />
        <span className="text-caption text-slate">%</span>
      </div>
    </div>
  );
}
