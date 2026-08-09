'use client';

/**
 * CRM collateral-haircut register editor (Basel II ¶151 supervisory
 * haircuts). View mode shows the resolved schedule — the code defaults with
 * any Board-adopted rows over them; edit mode records a new effective-dated
 * generation for the collateral classes whose haircut changed, carrying the
 * Board evidence (approved-by + required audit reason). Approver-gated
 * server-side; the capital engine reads this register on every run.
 */

import { useMemo, useState } from 'react';
import type {
  CrmHaircutRead,
  CrmHaircutRegisterRead,
  CrmHaircutUpdate,
} from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { SkeletonTable } from '@/components/ui/Skeleton';
import { useCrmHaircutRegister, useUpdateCrmHaircutRegister } from '@/lib/api/hooks';
import { fmtDateUTC, labelize, num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import { FormActions, ReasonField, textOrNull } from '@/components/institution/shared';
import {
  EditRegisterAction,
  EvidenceFields,
  numericInputCls,
  parseDecimalInput,
  sameDecimal,
  useApproverGate,
} from './common';

const viewColumns: Column<CrmHaircutRead>[] = [
  {
    key: 'class',
    header: 'Collateral class',
    width: '34%',
    render: (r) => (
      <span className="font-mono text-body text-navy">{r.collateralClass}</span>
    ),
  },
  {
    key: 'haircut',
    header: 'Haircut',
    numeric: true,
    render: (r) => fmtPct(num(r.haircutPct), 2),
  },
  {
    key: 'source',
    header: 'Source',
    render: (r) =>
      r.isDefault ? (
        <StatusPill tone="slate">Basel ¶151 default</StatusPill>
      ) : (
        <StatusPill tone="success">Board register</StatusPill>
      ),
  },
  {
    key: 'evidence',
    header: 'Approval evidence',
    render: (r) =>
      r.approvedBy ? (
        <div>
          <p className="text-body text-navy">{String(r.approvedBy)}</p>
          <p className="text-caption text-slate">
            Effective{' '}
            {r.effectiveFrom ? fmtDateUTC(new Date(String(r.effectiveFrom))) : '—'}
          </p>
        </div>
      ) : (
        <span className="text-caption text-slate">
          Code default — no Board row yet
        </span>
      ),
  },
];

export default function CrmHaircutCard({ bankId }: { bankId: string }) {
  const { isApprover } = useApproverGate();
  const query = useCrmHaircutRegister(bankId);
  const [editing, setEditing] = useState(false);

  return (
    <SectionCard
      title="CRM haircut register"
      subtitle="Supervisory collateral haircuts for credit risk mitigation — the Basel ¶151 code defaults under the Board's adopted rows"
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
          Each save records an effective-dated generation —
          {` ${query.data?.history.length ?? 0} on record. `}
          The capital engine applies these haircuts to `crm_collateral` facts on
          every run; updates are approver-gated and audited.
        </span>
      }
    >
      {query.isLoading ? (
        <SkeletonTable rows={5} />
      ) : query.error ? (
        <div className="p-5">
          <ErrorPanel
            error={query.error}
            title="Could not load the CRM haircut register"
            onRetry={() => query.refetch()}
          />
        </div>
      ) : query.data ? (
        editing ? (
          <CrmEditor
            bankId={bankId}
            register={query.data}
            onClose={() => setEditing(false)}
          />
        ) : (
          <DataTable columns={viewColumns} rows={query.data.haircuts} density="compact" />
        )
      ) : null}
    </SectionCard>
  );
}

function CrmEditor({
  bankId,
  register,
  onClose,
}: {
  bankId: string;
  register: CrmHaircutRegisterRead;
  onClose: () => void;
}) {
  const update = useUpdateCrmHaircutRegister(bankId);

  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const row of register.haircuts) {
      initial[row.collateralClass] = String(row.haircutPct);
    }
    return initial;
  });
  const [effectiveFrom, setEffectiveFrom] = useState('');
  const [approvedBy, setApprovedBy] = useState('');
  const [notes, setNotes] = useState('');
  const [reason, setReason] = useState('');

  const currentByClass = useMemo(
    () =>
      new Map(register.haircuts.map((row) => [row.collateralClass, String(row.haircutPct)])),
    [register]
  );

  const { changed, invalid } = useMemo(() => {
    const changedClasses: Record<string, string> = {};
    const invalidClasses: string[] = [];
    for (const [cls, input] of Object.entries(values)) {
      if (sameDecimal(input, currentByClass.get(cls) ?? null)) continue;
      const parsed = parseDecimalInput(input);
      if (parsed === null || parsed < 0) {
        invalidClasses.push(cls);
        continue;
      }
      changedClasses[cls] = input.trim();
    }
    return { changed: changedClasses, invalid: invalidClasses };
  }, [values, currentByClass]);

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
    const payload: CrmHaircutUpdate = {
      haircuts: changed,
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
        Record a Board-approved haircut generation
      </p>

      <div className="space-y-2">
        {register.haircuts.map((row) => (
          <div
            key={row.collateralClass}
            className="flex items-center justify-between gap-4 rounded border border-border-light px-3 py-2"
          >
            <div className="min-w-0">
              <label
                htmlFor={`crm-${row.collateralClass}`}
                className="block text-body font-medium text-navy"
              >
                {labelize(row.collateralClass.toLowerCase())}
              </label>
              <p className="text-caption text-slate">
                <span className="font-mono">{row.collateralClass}</span> ·{' '}
                {row.isDefault
                  ? `Basel ¶151 default ${fmtPct(num(row.haircutPct), 2)}`
                  : `Board level ${fmtPct(num(row.haircutPct), 2)}`}
              </p>
            </div>
            <div className="shrink-0 flex items-center gap-1.5">
              <input
                id={`crm-${row.collateralClass}`}
                inputMode="decimal"
                value={values[row.collateralClass] ?? ''}
                onChange={(e) =>
                  setValues((prev) => ({
                    ...prev,
                    [row.collateralClass]: e.target.value,
                  }))
                }
                className={`${numericInputCls} w-28 ${
                  invalid.includes(row.collateralClass) ? 'border-critical' : ''
                }`}
                aria-invalid={invalid.includes(row.collateralClass)}
              />
              <span className="text-caption text-slate">%</span>
            </div>
          </div>
        ))}
      </div>

      <EvidenceFields
        idPrefix="crm"
        effectiveFrom={effectiveFrom}
        onEffectiveFrom={setEffectiveFrom}
        approvedBy={approvedBy}
        onApprovedBy={setApprovedBy}
        notes={notes}
        onNotes={setNotes}
      />

      <div className="max-w-xl">
        <ReasonField id="crm-reason" value={reason} onChange={setReason} />
      </div>

      <p className="text-caption text-slate">
        {changedCount === 0
          ? 'No haircuts changed yet — only changed collateral classes are recorded.'
          : `${changedCount} collateral class${changedCount === 1 ? '' : 'es'} will be recorded in this generation.`}
      </p>

      {update.error && (
        <ErrorPanel error={update.error} title="Could not record the haircut generation" />
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
