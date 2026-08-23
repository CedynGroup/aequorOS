'use client';

import { useMemo, useState } from 'react';
import { Database, Lock, Shield } from 'lucide-react';
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
import { InspectTenantButton } from '@/components/tenants/InspectTenantButton';
import { isInspectionRequired } from '@/components/tenants/util';

/**
 * Desk dataset entitlements (spec §10) — which published products one bank
 * receives. Two grant shapes: a TIER (core / standard / premium bundle) or a
 * single DATASET override; default when a bank has no rows is standard. Revoke
 * end-dates a grant append-only.
 *
 * The page works ONE bank at a time, inside an inspection session. An
 * entitlement is that bank's commercial terms, so viewing or changing it is a
 * look inside the tenant — the same audited control every other deep tenant
 * surface passes. The page used to open on every bank's grants at once, with
 * no session and no audit record of the read.
 */

type Tier = 'core' | 'standard' | 'premium';

export default function EntitlementsPage() {
  const [includeRevoked, setIncludeRevoked] = useState(false);
  const [orgId, setOrgId] = useState('');
  const selectedOrg = orgId.trim();
  const { data, error, loading, reload } = useApi(
    () =>
      selectedOrg
        ? listDeskEntitlements({ organizationId: selectedOrg, includeRevoked })
        : Promise.resolve(null),
    [selectedOrg, includeRevoked],
  );
  const gated = isInspectionRequired(error);
  // `useApi` keeps the PREVIOUS result on screen while the next one is in
  // flight, and this table no longer carries a bank column (the page is
  // single-bank now). Between picking bank B and its rows arriving, the
  // previous bank's grants would therefore render under bank B's heading. Show
  // rows only once they are settled AND every one of them belongs to the bank
  // currently selected — a stale render of another bank's commercial terms is
  // exactly what this page was rebuilt to prevent.
  const grants =
    !loading && data && data.entitlements.every((e) => e.organization_id === selectedOrg)
      ? data
      : null;

  const [grantMode, setGrantMode] = useState<'tier' | 'dataset'>('tier');
  const [tier, setTier] = useState<Tier>('standard');
  const [datasetCode, setDatasetCode] = useState('');
  const [effectiveFrom, setEffectiveFrom] = useState(() => new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState('');

  const catalog = data?.catalog;
  const datasets = catalog?.datasets ?? [];

  const grantTier = useMutation(grantDeskEntitlementTier, {
    successMessage: `Granted the ${tier} tier to ${selectedOrg}`,
    errorContext: 'Grant tier',
    onSuccess: () => reload(),
  });
  const grantDataset = useMutation(grantDeskEntitlementDataset, {
    successMessage: `Granted ${datasetCode} to ${selectedOrg}`,
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
    if (!selectedOrg) return;
    if (grantMode === 'tier') {
      void grantTier.mutate({
        organization_id: selectedOrg,
        tier,
        effective_from: effectiveFrom,
        notes: notes.trim() || undefined,
      });
    } else {
      if (!datasetCode.trim()) return;
      void grantDataset.mutate({
        organization_id: selectedOrg,
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
              onClick={() => void revoke.mutate(e.id, e.organization_id)}
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
        sub="Which desk-published datasets a bank receives. A bank with no grants receives the standard tier. Premium adds the corporate credit curve."
      />

      {/* ------------------------------------------------------ bank picker */}
      <SectionCard
        title="Bank"
        subtitle="Entitlements are one bank's commercial terms, so this page works one bank at a time inside an audited inspection session."
        className="mb-5"
      >
        <OrgSelect value={orgId} onChange={setOrgId} label="Bank" required className="w-72" />
      </SectionCard>

      {!selectedOrg && (
        <SectionCard title="Grants">
          <EmptyState
            title="Choose a bank to see its entitlements"
            description="Every view and every change is recorded against that bank in the operator audit log."
          />
        </SectionCard>
      )}

      {selectedOrg && gated && (
        <SectionCard title="Grants" subtitle="Audited inspection required">
          <EmptyState
            Icon={Lock}
            title="Start an inspection session to view this bank's entitlements"
            description="Entitlements decide which desk data this bank receives. Viewing or changing them opens a time-boxed session that is written to the operator audit log."
            action={<InspectTenantButton orgId={selectedOrg} />}
          />
        </SectionCard>
      )}

      {/* -------------------------------------------------- tier catalog */}
      {selectedOrg && !gated && catalog && (
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
      {selectedOrg && !gated && datasets.length > 0 && (
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
      {selectedOrg && !gated && (
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
                disabled={!selectedOrg || (grantMode === 'dataset' && !datasetCode.trim())}
              >
                {grantMode === 'tier' ? 'Grant tier' : 'Grant dataset'}
              </Button>
            </div>
          </div>
        </form>
      </SectionCard>
      )}

      {/* -------------------------------------------------- grants table */}
      {selectedOrg && !gated && (
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
        {error && !gated && (
          <div className="p-4">
            <ErrorPanel error={error} onRetry={reload} context="Loading entitlements" />
          </div>
        )}
        {grants && grants.entitlements.length === 0 && (
          <EmptyState
            title="No explicit grants"
            hint="A bank with no grants receives the standard tier automatically, at publish and at read time."
          />
        )}
        {grants && grants.entitlements.length > 0 && (
          <DataTable
            columns={columns}
            rows={grants.entitlements}
            density="compact"
            pageSize={25}
            getFilterText={(e) => `${e.dataset_code} ${e.tier ?? ''} ${e.status}`}
            filterPlaceholder="Filter by dataset, tier or status…"
          />
        )}
      </SectionCard>
      )}
    </div>
  );
}
