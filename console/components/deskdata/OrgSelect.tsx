'use client';

import { useId, useMemo } from 'react';
import { listTenants } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { Field, Input } from '@/components/ui';

/**
 * Organization picker for the desk cluster. Backed by the operator tenant
 * roster (`listTenants`) so an operator selects a real onboarded institution
 * by name instead of pasting an OR- platform id — but the underlying value is
 * still the platform id (`OR-XXXXXXXX`), which is what every desk grant
 * endpoint takes. A datalist keeps it a free-typed field too: an org that
 * hasn't loaded (or a brand-new id) can still be entered by hand.
 */
export function OrgSelect({
  value,
  onChange,
  label = 'Organization',
  required = false,
  className = '',
}: {
  value: string;
  onChange: (next: string) => void;
  label?: string;
  required?: boolean;
  className?: string;
}) {
  const listId = useId();
  const { data } = useApi(() => listTenants(), []);
  const tenants = data?.tenants ?? [];

  const matchedName = useMemo(() => {
    const trimmed = value.trim();
    if (!trimmed) return null;
    return tenants.find((t) => t.organization_id === trimmed)?.organization_name ?? null;
  }, [tenants, value]);

  return (
    <Field
      label={label}
      required={required}
      className={className}
      hint={matchedName ?? (tenants.length > 0 ? 'Start typing to search onboarded institutions.' : undefined)}
    >
      <Input
        list={listId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="OR-XXXXXXXX"
        className="font-mono"
        spellCheck={false}
        autoComplete="off"
      />
      <datalist id={listId}>
        {tenants.map((t) => (
          <option key={t.organization_id} value={t.organization_id}>
            {t.organization_name}
            {t.bank_name ? ` — ${t.bank_name}` : ''}
          </option>
        ))}
      </datalist>
    </Field>
  );
}
