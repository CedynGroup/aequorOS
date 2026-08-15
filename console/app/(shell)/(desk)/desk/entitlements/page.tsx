'use client';

import { useState } from 'react';
import { Loader2, Shield } from 'lucide-react';
import {
  grantDeskEntitlementTier,
  listDeskEntitlements,
  revokeDeskEntitlement,
  toApiError,
  type ApiError,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, DASH } from '@/lib/format';
import { Chip, EmptyState, ErrorPanel, PageHeader, SkeletonRows } from '@/components/ui';

/**
 * Desk dataset entitlement tiers (spec §10) — which published products each
 * bank org receives: core / standard / premium.
 */

export default function EntitlementsPage() {
  const { data, error, loading, reload } = useApi(() => listDeskEntitlements());
  const [orgId, setOrgId] = useState('');
  const [tier, setTier] = useState<'core' | 'standard' | 'premium'>('standard');
  const [effectiveFrom, setEffectiveFrom] = useState(() =>
    new Date().toISOString().slice(0, 10),
  );
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);

  async function grant() {
    if (!orgId.trim()) return;
    setBusy(true);
    setActionError(null);
    try {
      await grantDeskEntitlementTier({
        organization_id: orgId.trim(),
        tier,
        effective_from: effectiveFrom,
      });
      reload();
    } catch (err) {
      setActionError(toApiError(err));
    }
    setBusy(false);
  }

  async function revoke(id: string) {
    setBusy(true);
    setActionError(null);
    try {
      await revokeDeskEntitlement(id);
      reload();
    } catch (err) {
      setActionError(toApiError(err));
    }
    setBusy(false);
  }

  const catalog = data?.catalog;
  const inputClass =
    'rounded-md border border-border bg-surface-base px-3 py-2 text-body text-ink focus:border-focus focus:outline-none';

  return (
    <div>
      <PageHeader
        title="Entitlements"
        sub="Which desk-published datasets each bank receives (spec §10). Default when no rows: standard tier. Premium unlocks the Stage 3 credit curve."
      />

      {catalog && (
        <div className="mb-5 grid gap-3 md:grid-cols-3">
          {Object.entries(catalog.tiers ?? {}).map(([name, datasets]) => (
            <div key={name} className="card p-4">
              <div className="flex items-center gap-2">
                <Shield size={14} className="text-action" />
                <h2 className="text-body font-medium text-navy capitalize">{name}</h2>
                {catalog.default_tier === name && <Chip tone="accent">default</Chip>}
              </div>
              <ul className="mt-2 space-y-0.5">
                {(datasets as string[]).map((d) => (
                  <li key={d} className="font-mono text-micro text-slate">
                    {d}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      <div className="card mb-5 p-4">
        <h2 className="text-h3 text-navy">Grant tier</h2>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1 block text-caption font-medium text-slate">
              Organization id
            </span>
            <input
              className={`${inputClass} w-48 font-mono`}
              value={orgId}
              onChange={(e) => setOrgId(e.target.value)}
              placeholder="OR-XXXXXXXX"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-caption font-medium text-slate">Tier</span>
            <select
              className={inputClass}
              value={tier}
              onChange={(e) => setTier(e.target.value as typeof tier)}
            >
              <option value="core">core</option>
              <option value="standard">standard</option>
              <option value="premium">premium</option>
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-caption font-medium text-slate">
              Effective from
            </span>
            <input
              type="date"
              className={inputClass}
              value={effectiveFrom}
              onChange={(e) => setEffectiveFrom(e.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={busy || !orgId.trim()}
            onClick={() => void grant()}
            className="btn-primary inline-flex items-center gap-1.5 px-3 py-2 text-body disabled:opacity-50"
          >
            {busy && <Loader2 size={14} className="animate-spin" />}
            Grant tier
          </button>
        </div>
        {actionError && (
          <div className="mt-3">
            <ErrorPanel error={actionError} context="Entitlement action" />
          </div>
        )}
      </div>

      <div className="card overflow-x-auto">
        {loading && <SkeletonRows rows={5} />}
        {error && (
          <div className="p-4">
            <ErrorPanel error={error} onRetry={reload} context="Loading entitlements" />
          </div>
        )}
        {data && data.entitlements.length === 0 && (
          <EmptyState
            title="No explicit grants"
            hint="Banks without rows receive the standard tier automatically at publish and read time."
          />
        )}
        {data && data.entitlements.length > 0 && (
          <table className="w-full text-body">
            <thead>
              <tr className="border-b border-border-light bg-surface text-left text-micro uppercase tracking-wide text-slate">
                <th className="px-4 py-2.5 font-medium">Org</th>
                <th className="px-4 py-2.5 font-medium">Dataset</th>
                <th className="px-4 py-2.5 font-medium">Tier</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">From</th>
                <th className="px-4 py-2.5 font-medium">To</th>
                <th className="px-4 py-2.5 font-medium">Granted by</th>
                <th className="px-4 py-2.5 font-medium" />
              </tr>
            </thead>
            <tbody>
              {data.entitlements.map((e) => (
                <tr key={e.id} className="border-b border-border-light last:border-b-0">
                  <td className="px-4 py-2 font-mono text-caption">{e.organization_id}</td>
                  <td className="px-4 py-2 font-mono text-caption">{e.dataset_code}</td>
                  <td className="px-4 py-2 text-caption">{e.tier ?? DASH}</td>
                  <td className="px-4 py-2">
                    <Chip tone={e.status === 'active' ? 'ok' : 'neutral'}>{e.status}</Chip>
                  </td>
                  <td className="px-4 py-2 text-caption">{fmtDate(e.effective_from)}</td>
                  <td className="px-4 py-2 text-caption">
                    {e.effective_to ? fmtDate(e.effective_to) : DASH}
                  </td>
                  <td className="px-4 py-2 font-mono text-micro">{e.granted_by}</td>
                  <td className="px-4 py-2 text-right">
                    {e.status === 'active' && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void revoke(e.id)}
                        className="text-caption text-critical hover:underline disabled:opacity-50"
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
