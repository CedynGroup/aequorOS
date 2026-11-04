'use client';

/**
 * Shared grammar for the Board Registers editor surface (Governance →
 * Board Registers). Each card shares the same configuration ceremony:
 * an Edit action gated on scoped configure authority, effective-dated
 * approval evidence (approved by + effective from), and the required audit
 * reason. Values travel as strings end-to-end so no decimal precision is
 * lost between the form and the generated contract.
 */

import { useId } from 'react';
import { Pencil } from 'lucide-react';
import { Field, inputCls } from '@/components/institution/shared';

// ---------------------------------------------------------------------------
// Configuration gating — read/navigation authority remains independent.
// ---------------------------------------------------------------------------

/**
 * Disabled controls retain a visible, accessible explanation.
 */
export function EditRegisterAction({
  allowed,
  reason,
  editing,
  onEdit,
  label = 'Edit register',
}: {
  allowed: boolean;
  reason: string;
  editing: boolean;
  onEdit: () => void;
  label?: string;
}) {
  const reasonId = useId();
  if (editing && allowed) return null;
  if (!allowed) {
    return (
      <span className="inline-flex flex-wrap items-center justify-end gap-2 max-w-56 sm:max-w-64">
        <button
          type="button"
          disabled
          title={reason}
          aria-describedby={reasonId}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-slate border border-border rounded-md opacity-60 cursor-not-allowed"
        >
          <Pencil size={13} aria-hidden />
          {label}
        </button>
        <span id={reasonId} className="text-micro text-slate">{reason}</span>
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
