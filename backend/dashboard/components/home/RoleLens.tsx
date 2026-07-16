'use client';

/**
 * Role lens — a client-side view permutation for the Command Center.
 *
 * Each lens reorders the same panels (no separate pages, no data changes):
 * Risk leads with the module pulse wall sorted by breach severity, CFO leads
 * with capital and the balance sheet, ALM leads with rate/funding modules.
 * The choice persists in localStorage.
 */

import { useCallback, useEffect, useState } from 'react';
import type { LiveModule } from '@aequoros/risk-service-api';

export type RoleLens = 'treasurer' | 'alm' | 'risk' | 'cfo';

export type PanelKey = 'pulse' | 'balance' | 'band' | 'freshness';

/** 'severity' sorts pulse cards red → amber → green instead of a fixed order. */
export type ModuleOrder = LiveModule[] | 'severity';

export const ROLE_CONFIG: Record<
  RoleLens,
  { label: string; panels: PanelKey[]; moduleOrder: ModuleOrder }
> = {
  treasurer: {
    label: 'Treasurer',
    panels: ['pulse', 'balance', 'band', 'freshness'],
    moduleOrder: ['liquidity', 'fx', 'irr', 'capital', 'ftp', 'forecast'],
  },
  alm: {
    label: 'ALM',
    panels: ['pulse', 'band', 'balance', 'freshness'],
    moduleOrder: ['irr', 'liquidity', 'ftp', 'fx', 'capital', 'forecast'],
  },
  risk: {
    label: 'Risk',
    panels: ['pulse', 'band', 'freshness', 'balance'],
    moduleOrder: 'severity',
  },
  cfo: {
    label: 'CFO',
    panels: ['balance', 'pulse', 'band', 'freshness'],
    moduleOrder: ['capital', 'ftp', 'forecast', 'liquidity', 'irr', 'fx'],
  },
};

const ROLES: RoleLens[] = ['treasurer', 'alm', 'risk', 'cfo'];

const STORAGE_KEY = 'aequoros.home.role-lens';

/** Persisted role lens. Reads localStorage after mount to stay SSR-safe. */
export function useRoleLens(): [RoleLens, (role: RoleLens) => void] {
  const [role, setRole] = useState<RoleLens>('treasurer');

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && ROLES.includes(stored as RoleLens)) {
      setRole(stored as RoleLens);
    }
  }, []);

  const update = useCallback((next: RoleLens) => {
    setRole(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage unavailable (private mode) — the lens still works in-session.
    }
  }, []);

  return [role, update];
}

/** Segmented Treasurer · ALM · Risk · CFO switcher for the page header. */
export default function RoleLensTabs({
  role,
  onChange,
}: {
  role: RoleLens;
  onChange: (role: RoleLens) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Role lens"
      className="inline-flex items-center rounded-md border border-border bg-surface-base p-0.5"
    >
      {ROLES.map((key) => {
        const active = key === role;
        return (
          <button
            key={key}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(key)}
            className={`px-3 py-1.5 rounded text-caption font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-focus ${
              active
                ? 'bg-surface-raised text-navy shadow-subtle'
                : 'text-slate hover:text-navy'
            }`}
          >
            {ROLE_CONFIG[key].label}
          </button>
        );
      })}
    </div>
  );
}
