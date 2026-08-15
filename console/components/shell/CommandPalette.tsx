'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, Building2, Search, type LucideIcon } from 'lucide-react';
import { listTenants } from '@/lib/api';
import { ALL_NAV_ITEMS } from './nav';

export type PaletteHit = {
  id: string;
  label: string;
  sub?: string;
  href: string;
  icon: LucideIcon;
  group: string;
};

/** Async source of tenant hits for the palette (by name / BK- / OR-). */
export type TenantSearchProvider = (query: string) => Promise<PaletteHit[]>;

// Cache the tenant inventory once per session; the palette filters it client-side.
let tenantsCache: ReturnType<typeof listTenants> | null = null;

const defaultTenantSearch: TenantSearchProvider = async (query) => {
  if (!query.trim()) return [];
  tenantsCache ??= listTenants();
  const res = await tenantsCache;
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  return res.tenants
    .filter((t) => {
      const hay = `${t.organization_name} ${t.bank_name ?? ''} ${t.organization_id} ${
        t.bank_id ?? ''
      }`.toLowerCase();
      return terms.every((term) => hay.includes(term));
    })
    .slice(0, 8)
    .map<PaletteHit>((t) => ({
      id: t.organization_id,
      label: t.bank_name ?? t.organization_name,
      sub: `${t.organization_id}${t.bank_id ? ` · ${t.bank_id}` : ''}`,
      href: `/tenants/${t.organization_id}`,
      icon: Building2,
      group: 'Tenant matches',
    }));
};

const NAV_HITS: PaletteHit[] = ALL_NAV_ITEMS.map((item) => ({
  id: item.href,
  label: item.label,
  href: item.href,
  icon: item.icon,
  group: 'Navigate',
  sub: undefined,
}));

const NAV_HAYSTACK = new Map<string, string>(
  ALL_NAV_ITEMS.map((item) => [
    item.href,
    `${item.label} ${item.keywords ?? ''}`.toLowerCase(),
  ]),
);

/**
 * ⌘K command palette. Fuzzy AND-filters the static rail nav and merges an
 * async tenant source (default: listTenants, filtered by name / BK- / OR-).
 * A feature agent can swap in a server-side tenant search via `tenantSearch`.
 */
export default function CommandPalette({
  open,
  onClose,
  tenantSearch = defaultTenantSearch,
}: {
  open: boolean;
  onClose: () => void;
  tenantSearch?: TenantSearchProvider;
}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [tenantHits, setTenantHits] = useState<PaletteHit[]>([]);
  const router = useRouter();

  const navHits = useMemo(() => {
    if (!query.trim()) return NAV_HITS;
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    return NAV_HITS.filter((hit) => {
      const hay = NAV_HAYSTACK.get(hit.id) ?? hit.label.toLowerCase();
      return terms.every((t) => hay.includes(t));
    });
  }, [query]);

  // Debounced async tenant lookup.
  useEffect(() => {
    if (!open || !query.trim()) {
      setTenantHits((prev) => (prev.length ? [] : prev));
      return;
    }
    let alive = true;
    const handle = window.setTimeout(() => {
      tenantSearch(query)
        .then((hits) => alive && setTenantHits(hits))
        .catch(() => alive && setTenantHits([]));
    }, 150);
    return () => {
      alive = false;
      window.clearTimeout(handle);
    };
  }, [open, query, tenantSearch]);

  const hits = useMemo(() => [...navHits, ...tenantHits], [navHits, tenantHits]);

  useEffect(() => setActive(0), [query, tenantHits.length]);

  // Reset transient state when the palette closes. Depends ONLY on `open` —
  // depending on `hits` here would loop forever, because clearing tenantHits
  // changes `hits`, which would retrigger the effect (Maximum update depth).
  useEffect(() => {
    if (!open) {
      setQuery('');
      setTenantHits((prev) => (prev.length ? [] : prev));
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActive((a) => Math.min(hits.length - 1, a + 1));
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActive((a) => Math.max(0, a - 1));
      }
      if (e.key === 'Enter' && hits[active]) {
        e.preventDefault();
        router.push(hits[active].href);
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, hits, active, onClose, router]);

  if (!open) return null;

  // Group in a stable order while preserving the flat `active` index.
  const groups: { label: string; hits: PaletteHit[] }[] = [];
  for (const hit of hits) {
    const existing = groups.find((g) => g.label === hit.group);
    if (existing) existing.hits.push(hit);
    else groups.push({ label: hit.group, hits: [hit] });
  }
  let runningIdx = 0;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      className="fixed inset-0 z-[60] flex items-start justify-center px-4 pt-24"
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
      />
      <div className="relative w-full max-w-xl overflow-hidden rounded-lg border border-border bg-surface-raised shadow-pop">
        <div className="flex items-center gap-3 border-b border-border-light px-4 py-3">
          <Search size={16} className="text-slate" aria-hidden />
          <input
            autoFocus
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search screens and tenants (name / BK- / OR-)…"
            className="flex-1 bg-transparent text-body text-navy outline-none placeholder:text-slate"
          />
          <kbd className="rounded border border-border-light bg-surface px-1.5 py-0.5 font-mono text-[10px] text-slate">
            ESC
          </kbd>
        </div>

        <div className="max-h-[420px] overflow-y-auto py-2">
          {hits.length === 0 ? (
            <div className="px-5 py-8 text-center text-body text-slate">
              No results for &ldquo;{query}&rdquo;
            </div>
          ) : (
            groups.map((group) => (
              <div key={group.label} className="mb-2">
                <p className="px-4 py-1.5 text-micro font-medium uppercase tracking-wider text-slate">
                  {group.label}
                </p>
                <ul>
                  {group.hits.map((hit) => {
                    const idx = runningIdx++;
                    const isActive = idx === active;
                    const Icon = hit.icon;
                    return (
                      <li key={`${hit.group}:${hit.id}`}>
                        <button
                          type="button"
                          onMouseEnter={() => setActive(idx)}
                          onClick={() => {
                            router.push(hit.href);
                            onClose();
                          }}
                          className={`flex w-full items-center gap-3 px-4 py-2 text-left text-body ${
                            isActive ? 'bg-action-light text-navy' : 'text-navy/85 hover:bg-surface'
                          }`}
                        >
                          <Icon
                            size={14}
                            className={isActive ? 'text-action' : 'text-slate'}
                            aria-hidden
                          />
                          <span className="flex-1 truncate">{hit.label}</span>
                          {hit.sub && (
                            <span className="shrink-0 font-mono text-caption text-slate">
                              {hit.sub}
                            </span>
                          )}
                          <ArrowRight
                            size={12}
                            className={isActive ? 'text-action' : 'text-transparent'}
                            aria-hidden
                          />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </div>

        <div className="flex items-center gap-4 border-t border-border-light px-4 py-2 text-caption text-slate">
          <span className="inline-flex items-center gap-1">
            <kbd className="rounded border border-border-light bg-surface px-1.5 py-0.5 font-mono text-[10px]">
              ↑↓
            </kbd>
            navigate
          </span>
          <span className="inline-flex items-center gap-1">
            <kbd className="rounded border border-border-light bg-surface px-1.5 py-0.5 font-mono text-[10px]">
              ↵
            </kbd>
            select
          </span>
          <span className="ml-auto tnum">{hits.length} results</span>
        </div>
      </div>
    </div>
  );
}
