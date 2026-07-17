'use client';

/**
 * Domain coverage catalog for a connection mode: every core-banking domain with
 * its category, canonical entity type, default pull cadence, and whether the
 * mode catalog supports it. Reads the live catalog from the backend — coverage
 * is defined at runtime, not by a static table.
 */

import { useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import type { TemenosDomainInfoRead } from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import { useTemenosDomains } from '@/lib/api/hooks';
import {
  DOMAIN_CATEGORY_LABELS,
  ENTITY_TYPE_LABELS,
  MODES,
  cadenceLabel,
  type ModeKey,
} from './shared';

export default function DomainCoverage({ bankId }: { bankId: string | undefined }) {
  const [mode, setMode] = useState<ModeKey>('OFS');
  const query = useTemenosDomains(bankId, mode);

  const groups = useMemo(() => {
    const byCategory = new Map<string, TemenosDomainInfoRead[]>();
    for (const domain of query.data?.domains ?? []) {
      const group = byCategory.get(domain.category) ?? [];
      group.push(domain);
      byCategory.set(domain.category, group);
    }
    return [...byCategory.entries()];
  }, [query.data]);

  const supportedCount = (query.data?.domains ?? []).filter((d) => d.supported).length;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-md border border-border overflow-hidden">
          {MODES.map((option) => (
            <button
              key={option.key}
              type="button"
              onClick={() => setMode(option.key)}
              className={`px-3 py-1.5 text-caption font-medium border-r border-border last:border-r-0 ${
                mode === option.key
                  ? 'bg-action text-white'
                  : 'bg-surface-raised text-navy hover:bg-surface'
              }`}
            >
              {option.name}
            </button>
          ))}
        </div>
        {query.data && (
          <p className="text-caption text-slate">
            <span className="font-mono font-medium text-navy">{supportedCount}</span> of{' '}
            <span className="font-mono">{query.data.domains.length}</span> domains supported
            over {MODES.find((m) => m.key === mode)?.name}
          </p>
        )}
      </div>

      {query.isPending ? (
        <div className="card p-6 text-body text-slate inline-flex items-center gap-2">
          <Loader2 size={14} className="animate-spin" aria-hidden />
          Loading the domain catalog…
        </div>
      ) : (
        <div className="card overflow-hidden">
          <table className="w-full text-body">
            <thead>
              <tr className="border-b border-border-light text-caption uppercase tracking-wider text-slate">
                <th className="text-left font-medium px-4 py-2.5">Domain</th>
                <th className="text-left font-medium px-4 py-2.5">Canonical entity</th>
                <th className="text-left font-medium px-4 py-2.5">Default cadence</th>
                <th className="text-left font-medium px-4 py-2.5">Coverage</th>
              </tr>
            </thead>
            <tbody>
              {groups.map(([category, domains]) => (
                <CategoryRows
                  key={category}
                  category={category}
                  domains={domains}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CategoryRows({
  category,
  domains,
}: {
  category: string;
  domains: TemenosDomainInfoRead[];
}) {
  return (
    <>
      <tr className="bg-surface-alt">
        <td
          colSpan={4}
          className="px-4 py-1.5 text-caption font-medium uppercase tracking-wider text-slate"
        >
          {DOMAIN_CATEGORY_LABELS[category] ?? category}
        </td>
      </tr>
      {domains.map((domain) => (
        <tr key={domain.domain} className="border-b border-border-light last:border-0">
          <td className="px-4 py-2.5 font-mono text-navy">{domain.domain}</td>
          <td className="px-4 py-2.5 text-slate">
            {ENTITY_TYPE_LABELS[domain.entityType] ?? domain.entityType}
          </td>
          <td className="px-4 py-2.5 text-slate">{cadenceLabel(domain.defaultCadence)}</td>
          <td className="px-4 py-2.5">
            {domain.supported ? (
              <StatusPill tone="success">Supported</StatusPill>
            ) : (
              <StatusPill tone="slate">Staged</StatusPill>
            )}
          </td>
        </tr>
      ))}
    </>
  );
}
