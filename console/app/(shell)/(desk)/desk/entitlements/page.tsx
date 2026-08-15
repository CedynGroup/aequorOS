'use client';

import { useMemo, useState } from 'react';
import { Database, Shield } from 'lucide-react';
import {
  grantDeskEntitlementDataset,
  grantDeskEntitlementTier,
  listDeskEntitlements,
  revokeDeskEntitlement,
  type DeskEntitlement,
} from '@/lib/api';
import { useApi, useMutation } from '@/lib/use-api';
import { fmtDate, DASH } from '@/lib/format';
import {
  Button,
  Chip,
  type Column,
  DataTable,
  EmptyState,
  ErrorPanel,
  Field,
  Input,
  PageHeader,
  SectionCard,
  Select,
  SkeletonRows,
  SubTabs,
} from '@/components/ui';
import { OrgSelect } from '@/components/deskdata/OrgSelect';

/**
 * Desk dataset entitlements (spec §10) — which published products each bank org
 * receives. Two grant shapes: a TIER (core / standard / premium bundle) or a
 * single DATASET override; default when an org has no rows is standard. Revoke
 * end-dates a grant append-only.
 */

type Tier = 'core' | 'standard' | 'premium';

export default function EntitlementsPage() {
  const [includeRevoked, setIncludeRevoked] = useState(false);
  const { data, error, loading, reload } = useApi(
    () => listDeskEntitlements({ includeRevoked }),
    [includeRevoked],
  );

  const [grantMode, setGrantMode] = useState<'tier' | 'dataset'>('tier');
  const [orgId, setOrgId] = useState('');
  const [tier, setTier] = useState<Tier>('standard');
  const [datasetCode, setDatasetCode] = useState('');
  const [effectiveFrom, setEffectiveFrom] = useState(() => new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState('');

  const catalog = data?.catalog;
  const datasets = catalog?.datasets ?? [];

  const grantTier = useMutation(grantDeskEntitlementTier, {
    successMessage: `Granted ${tier} tier to ${orgId.trim()}`,
    errorContext: 'Grant tier',
    onSuccess: () => reload(),
  });
  const grantDataset = useMutation(grantDeskEntitlementDataset, {
    successMessage: `Granted ${datasetCode} to ${orgId.trim()}`,
    errorContext: 'Grant dataset',
    onSuccess: () => reload(),
  });
  const revoke = useMutation(revokeDeskEntitlement, {
    successMessage: 'Entitlement revoked',
    errorContext: 'Revoke entitlement',
    onSuccess: () => reload(),
  });

  const grantBusy = grantTier.loading || grantDataset.loading;

  function submitGrant() {
    if (!orgId.trim()) return;
    if (grantMode === 'tier') {
      void grantTier.mutate({
        organization_id: orgId.trim(),
        tier,
        effective_from: effectiveFrom,
        notes: notes.trim() || undefined,
      });
    } else {
      if (!datasetCode.trim()) return;
      void grantDataset.mutate({
        organization_id: orgId.trim(),
        dataset_code: datasetCode.trim(),
        effective_from: effectiveFrom,
        notes: notes.trim() || undefined,
      });
    }
  }

  const columns = useMemo<Column<DeskEntitlement>[]>(
    () => [
      {
        key: 'org',
        header: 'Org',
        sortable: true,
        sortAccessor: (e) => e.organization_id,
        render: (e) => <span className="font-mono text-caption">{e.organization_id}</span>,
      },
      {
        key: 'dataset',
        header: 'Dataset',
        sortable: true,
        sortAccessor: (e) => e.dataset_code,
        render: (e) => <span className="font-mono text-caption">{e.dataset_code}</span>,
      },
      { key: 'tier', header: 'Tier', render: (e) => <span className="text-caption">{e.tier ?? DASH}</span> },
      {
        key: 'status',
        header: 'Status',
        render: (e) => (
          <Chip tone={e.status === 'active' ? 'ok' : e.status === 'revoked' ? 'crit' : 'neutral'}>
            {e.status}
          </Chip>
        ),
      },
      { key: 'from', header: 'From', render: (e) => <span className="text-caption">{fmtDate(e.effective_from)}</span> },
      {
        key: 'to',
        header: 'To',
        render: (e) => <span className="text-caption">{e.effective_to ? fmtDate(e.effective_to) : DASH}</span>,
      },
      {
        key: 'granted_by',
        header: 'Granted by',
        render: (e) => <span className="font-mono text-micro">{e.granted_by}</span>,
      },
      {
        key: 'actions',
        header: '',
        align: 'right',
        render: (e) =>
          e.status === 'active' ? (
            <Button
              size="sm"
              variant="ghost"
              className="text-critical hover:text-critical"
              loading={revoke.loading}
              onClick={() => void revoke.mutate(e.id)}
            >
              Revoke
            </Button>
          ) : null,
      },
    ],
    [revoke],
  );

  return (
    <div>
      <PageHeader
        title="Entitlements"
        sub="Which desk-published datasets each bank receives (spec §10). Default when no rows: standard tier. Premium unlocks the Stage 3 credit curve."
      />

      {/* -------------------------------------------------- tier catalog */}
      {catalog && (
        <div className="mb-5 grid gap-4 md:grid-cols-3">
          {Object.entries(catalog.tiers ?? {}).map(([name, tierDatasets]) => (
            <SectionCard
              key={name}
              title={
                <span className="inline-flex items-center gap-2">
                  <Shield size={14} className="text-action" />
                  <span className="capitalize">{name}</span>
                  {catalog.default_tier === name && <Chip tone="accent">default</Chip>}
                </span>
              }
            >
              <ul className="space-y-0.5">
                {(tierDatasets as string[]).map((d) => (
                  <li key={d} className="font-mono text-micro text-slate">
                    {d}
                  </li>
                ))}
              </ul>
            </SectionCard>
          ))}
        </div>
      )}

      {/* ---------------------------------------------- dataset catalog */}
      {datasets.length > 0 && (
        <SectionCard
          title={
            <span className="inline-flex items-center gap-2">
              <Database size={14} className="text-action" /> Dataset catalog
            </span>
          }
          subtitle="Every grantable dataset — pick one below to grant a single-dataset override."
          className="mb-5"
        >
          <div className="flex flex-wrap gap-1.5">
            {datasets.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => {
                  setGrantMode('dataset');
                  setDatasetCode(d);
                }}
                className={`rounded border px-2 py-0.5 font-mono text-micro transition-colors ${
                  datasetCode === d
                    ? 'border-action/40 bg-action-light text-action'
                    : 'border-border-light bg-surface text-slate hover:text-navy'
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        </SectionCard>
      )}

      {/* --------------------------------------------------- grant panel */}
      <SectionCard title="Grant access" className="mb-5" noPadding>
        <SubTabs
          items={[
            { key: 'tier', label: 'By tier' },
            { key: 'dataset', label: 'By dataset' },
          ]}
          active={grantMode}
          onChange={(k) => setGrantMode(k as 'tier' | 'dataset')}
        />
        <form
          className="p-5"
          onSubmit={(e) => {
            e.preventDefault();
            submitGrant();
          }}
        >
          <div className="flex flex-wrap items-start gap-3">
            <OrgSelect value={orgId} onChange={setOrgId} required className="w-56" />
            {grantMode === 'tier' ? (
              <Field label="Tier" required>
                <Select value={tier} onChange={(e) => setTier(e.target.value as Tier)}>
                  <option value="core">core</option>
                  <option value="standard">standard</option>
                  <option value="premium">premium</option>
                </Select>
              </Field>
            ) : (
              <Field label="Dataset" required hint="Pick from the catalog above, or type a code.">
                <Input
                  list="entitlement-datasets"
                  className="w-56 font-mono"
                  value={datasetCode}
                  onChange={(e) => setDatasetCode(e.target.value)}
                  placeholder="GHS.CURVES.SOV"
                  spellCheck={false}
                />
                <datalist id="entitlement-datasets">
                  {datasets.map((d) => (
                    <option key={d} value={d} />
                  ))}
                </datalist>
              </Field>
            )}
            <Field label="Effective from" required>
              <Input type="date" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} />
            </Field>
            <Field label="Notes (optional)" className="min-w-[12rem] flex-1">
              <Input
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="e.g. contract ref, approval ticket"
              />
            </Field>
            <div className="mb-0.5 self-end">
              <Button
                type="submit"
                loading={grantBusy}
                disabled={!orgId.trim() || (grantMode === 'dataset' && !datasetCode.trim())}
              >
                {grantMode === 'tier' ? 'Grant tier' : 'Grant dataset'}
              </Button>
            </div>
          </div>
        </form>
      </SectionCard>

      {/* -------------------------------------------------- grants table */}
      <SectionCard
        title="Grants"
        actions={
          <label className="flex items-center gap-2 text-caption text-slate">
            <input
              type="checkbox"
              checked={includeRevoked}
              onChange={(e) => setIncludeRevoked(e.target.checked)}
            />
            Include revoked
          </label>
        }
        noPadding
      >
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
          <DataTable
            columns={columns}
            rows={data.entitlements}
            density="compact"
            pageSize={25}
            getFilterText={(e) => `${e.organization_id} ${e.dataset_code} ${e.tier ?? ''} ${e.status}`}
            filterPlaceholder="Filter by org, dataset, tier…"
          />
        )}
      </SectionCard>
    </div>
  );
}
