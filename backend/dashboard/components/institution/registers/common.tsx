'use client';

/**
 * Shared grammar for the Board Registers editor surface (Governance →
 * Board Registers). Every register here is Board configuration: the PUTs are
 * approver-gated server-side and audited, so each card shares the same
 * ceremony — an Edit action gated on the approver role, effective-dated
 * approval evidence (approved by + effective from), and the required audit
 * reason. Values travel as strings end-to-end so no decimal precision is
 * lost between the form and the generated contract.
 */

import { useSession } from 'next-auth/react';
import { Pencil } from 'lucide-react';
import { Field, inputCls } from '@/components/institution/shared';

// ---------------------------------------------------------------------------
// Role gating — the PUTs require the approver role server-side; the UI mirrors
// that gate so an analyst sees why the action is unavailable, not a 403.
// ---------------------------------------------------------------------------

/** Client-side mirror of the server's approver gate (approver or admin). */
export function useApproverGate(): { isApprover: boolean } {
  const { data: session } = useSession();
  const roles = session?.user?.roles ?? [];
  return { isApprover: roles.includes('approver') || roles.includes('admin') };
}

/**
 * The "Edit register" action for a SectionCard header: enabled for approvers,
 * disabled-with-reason for everyone else (the server enforces the same gate).
 */
export function EditRegisterAction({
  isApprover,
  editing,
  onEdit,
  label = 'Edit register',
}: {
  isApprover: boolean;
  editing: boolean;
  onEdit: () => void;
  label?: string;
}) {
  if (editing) return null;
  if (!isApprover) {
    return (
      <span className="inline-flex items-center gap-2">
        <button
          type="button"
          disabled
          title="Requires the approver role"
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-slate border border-border rounded-md opacity-60 cursor-not-allowed"
        >
          <Pencil size={13} aria-hidden />
          {label}
        </button>
        <span className="text-micro text-slate">Requires the approver role</span>
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onEdit}
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
    >
      <Pencil size={13} aria-hidden />
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Form primitives
// ---------------------------------------------------------------------------

/** Numeric register input — mono, right-aligned, tabular numerals. */
export const numericInputCls = `${inputCls} font-mono text-right tnum`;

/**
 * The effective-dated Board evidence every generation-based register PUT
 * carries: the date the generation takes effect and the approving authority
 * (Board minute reference or officer).
 */
export function EvidenceFields({
  idPrefix,
  effectiveFrom,
  onEffectiveFrom,
  approvedBy,
  onApprovedBy,
  notes,
  onNotes,
}: {
  idPrefix: string;
  effectiveFrom: string;
  onEffectiveFrom: (next: string) => void;
  approvedBy: string;
  onApprovedBy: (next: string) => void;
  notes?: string;
  onNotes?: (next: string) => void;
}) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <Field label="Effective from" htmlFor={`${idPrefix}-effective`} required>
        <input
          id={`${idPrefix}-effective`}
          type="date"
          value={effectiveFrom}
          onChange={(e) => onEffectiveFrom(e.target.value)}
          className={inputCls}
        />
      </Field>
      <Field
        label="Approved by"
        htmlFor={`${idPrefix}-approved-by`}
        required
        hint="Board minute reference or approving officer — recorded on the generation."
      >
        <input
          id={`${idPrefix}-approved-by`}
          value={approvedBy}
          onChange={(e) => onApprovedBy(e.target.value)}
          placeholder="e.g. Board minute BR-2026-014"
          className={inputCls}
        />
      </Field>
      {onNotes && (
        <Field label="Notes" htmlFor={`${idPrefix}-notes`}>
          <input
            id={`${idPrefix}-notes`}
            value={notes ?? ''}
            onChange={(e) => onNotes(e.target.value)}
            className={inputCls}
          />
        </Field>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Value helpers
// ---------------------------------------------------------------------------

/** Parse a register decimal input; null when empty or not a number. */
export function parseDecimalInput(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * True when the edited input still means the same number as the read-model
 * value ("80" vs "80.00"); guards the changed-rows-only payloads.
 */
export function sameDecimal(input: string, current: string | null | undefined): boolean {
  if (current == null) return input.trim() === '';
  const a = parseDecimalInput(input);
  const b = parseDecimalInput(String(current));
  return a !== null && b !== null && a === b;
}
