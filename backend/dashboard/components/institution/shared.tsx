'use client';

/**
 * Shared vocabulary + form primitives for the Institution Profile register:
 * enum labels and pills (party/outlet/product/licence statuses, role codes),
 * the standard labeled-field wrappers used by every register form, and the
 * required "Reason for change" field every canonical mutation carries.
 * Display copy only — the generated contracts stay the source of truth.
 */

import type { ReactNode } from 'react';
import { Loader2, Save } from 'lucide-react';
import type {
  LicenseStatus,
  OutletStatus,
  OutletType,
  PartyStatus,
  PartyType,
  ProductStatus,
  RelatedPartyRoleCode,
} from '@aequoros/risk-service-api';
import { RelatedPartyRoleCode as RoleCodes } from '@aequoros/risk-service-api';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { labelize } from '@/lib/api/values';

// ---------------------------------------------------------------------------
// Labels + pills
// ---------------------------------------------------------------------------

export const PARTY_TYPE_LABELS: Record<PartyType, string> = {
  individual: 'Individual',
  legal_entity: 'Legal entity',
};

export function PartyStatusPill({ status }: { status: PartyStatus }) {
  return (
    <StatusPill tone={status === 'active' ? 'success' : 'slate'}>
      {labelize(status)}
    </StatusPill>
  );
}

export const OUTLET_TYPE_LABELS: Record<OutletType, string> = {
  head_office: 'Head office',
  branch: 'Branch',
  agency: 'Agency',
};

export function OutletStatusPill({ status }: { status: OutletStatus }) {
  return (
    <StatusPill tone={status === 'active' ? 'success' : 'slate'}>
      {labelize(status)}
    </StatusPill>
  );
}

const PRODUCT_STATUS_TONES: Record<ProductStatus, StatusTone> = {
  proposed: 'amber',
  approved: 'success',
  withdrawn: 'slate',
};

export function ProductStatusPill({ status }: { status: ProductStatus }) {
  return (
    <StatusPill tone={PRODUCT_STATUS_TONES[status]}>{labelize(status)}</StatusPill>
  );
}

const LICENSE_STATUS_TONES: Record<LicenseStatus, StatusTone> = {
  active: 'success',
  revoked: 'critical',
  expired: 'slate',
};

export function LicenseStatusPill({ status }: { status: LicenseStatus }) {
  return (
    <StatusPill tone={LICENSE_STATUS_TONES[status]}>{labelize(status)}</StatusPill>
  );
}

/** Every register role code, in the generated contract's order. */
export const ROLE_OPTIONS: RelatedPartyRoleCode[] = Object.values(RoleCodes);

/** "chief_executive_officer" → "Chief Executive Officer". */
export function roleLabel(role: RelatedPartyRoleCode): string {
  return labelize(role);
}

// ---------------------------------------------------------------------------
// Form primitives — one input style across every register form.
// ---------------------------------------------------------------------------

export const inputCls =
  'w-full rounded border border-border bg-surface-raised px-2.5 py-1.5 text-body text-navy placeholder:text-slate-light';

export function Field({
  label,
  htmlFor,
  required = false,
  hint,
  children,
  className = '',
}: {
  label: string;
  htmlFor?: string;
  required?: boolean;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <label
        htmlFor={htmlFor}
        className="block text-caption font-medium text-navy mb-1"
      >
        {label}
        {required ? (
          <span className="font-normal text-slate"> (required)</span>
        ) : null}
      </label>
      {children}
      {hint && <p className="mt-1 text-micro text-slate leading-relaxed">{hint}</p>}
    </div>
  );
}

/** The canonical-mutation audit reason — required on every register write. */
export function ReasonField({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <Field label="Reason for change" htmlFor={id} required>
      <textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={2}
        placeholder="e.g. Registered the outcome of the March board resolution."
        className={inputCls}
      />
    </Field>
  );
}

/** Submit + cancel row for the register forms. */
export function FormActions({
  submitLabel,
  pending,
  disabled,
  onCancel,
}: {
  submitLabel: string;
  pending: boolean;
  disabled: boolean;
  onCancel: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <button
        type="submit"
        disabled={disabled || pending}
        className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
      >
        {pending ? (
          <Loader2 size={13} className="animate-spin" aria-hidden />
        ) : (
          <Save size={13} aria-hidden />
        )}
        {submitLabel}
      </button>
      <button
        type="button"
        onClick={onCancel}
        className="inline-flex items-center px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface"
      >
        Cancel
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Payload helpers — trimmed strings, empty → null. Decimals stay strings so
// no float precision is lost between the form and the generated contract.
// ---------------------------------------------------------------------------

export function textOrNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** Show a nullable read-model value, em-dash when unset. */
export function dash(value: string | null | undefined): string {
  return value != null && value !== '' ? value : '—';
}

/** Register date-only strings ("2020-06-30") → "30 Jun 2020"; "—" when unset. */
export function fmtRegisterDate(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(`${value}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime())) return value;
  return parsed.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}
