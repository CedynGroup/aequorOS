'use client';

import { useState, type ReactNode } from 'react';
import type {
  ApiError,
  TenantCapitalThreshold,
  TenantConfigBank,
  TenantConfigResponse,
  TenantIntegrationKey,
  TenantLiquidityThreshold,
  TenantMappingConfig,
} from '@/lib/api';
import { fmtDate, fmtTs, relTime, DASH } from '@/lib/format';
import {
  Chip,
  DataTable,
  Drawer,
  EmptyState,
  FieldRow,
  SectionCard,
  StatusChip,
  type Column,
} from '@/components/ui';
import { DeepSectionBody } from './DeepSectionBody';
import { formatJson } from './util';

/**
 * Non-secret diagnostic configuration for one tenant: banks, active source
 * mappings (raw config viewable in a drawer), the Board threshold register,
 * the SSO connection (whether a secret exists — never the secret itself), and
 * integration-key metadata (never the raw key or its hash).
 */

function Block({ title, hint, children }: { title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="border-b border-border-light last:border-b-0">
      <div className="px-4 pt-3 text-micro font-medium uppercase tracking-wider text-slate">
        {title}
      </div>
      {hint && <p className="px-4 pt-0.5 text-caption text-slate">{hint}</p>}
      {children}
    </div>
  );
}

function pct(value: number): string {
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
}

const bankColumns: Column<TenantConfigBank>[] = [
  {
    key: 'name',
    header: 'Bank',
    render: (b) => (
      <div className="min-w-0">
        <div className="text-body text-navy">{b.name}</div>
        <div className="font-mono text-micro text-slate">{b.bank_id}</div>
      </div>
    ),
  },
  {
    key: 'jurisdiction',
    header: 'Jurisdiction',
    render: (b) => <span className="font-mono text-caption">{b.jurisdiction_code}</span>,
  },
  {
    key: 'currency',
    header: 'Currency',
    render: (b) => <span className="font-mono text-caption">{b.currency}</span>,
  },
  {
    key: 'license',
    header: 'License',
    render: (b) => <span className="text-caption text-navy/90">{b.license_type}</span>,
  },
];

const liquidityColumns: Column<TenantLiquidityThreshold>[] = [
  {
    key: 'code',
    header: 'Threshold',
    render: (t) => <span className="font-mono text-caption text-navy">{t.threshold_code}</span>,
  },
  {
    key: 'class',
    header: 'Class',
    render: (t) => <span className="text-caption text-navy/90">{t.institution_class}</span>,
  },
  {
    key: 'pct',
    header: 'Value',
    align: 'right',
    render: (t) => <span className="font-mono text-caption">{pct(t.threshold_pct)}</span>,
  },
  {
    key: 'from',
    header: 'From',
    align: 'right',
    render: (t) => <span className="text-caption text-slate">{fmtDate(t.effective_from)}</span>,
  },
];

const capitalColumns: Column<TenantCapitalThreshold>[] = [
  {
    key: 'code',
    header: 'Threshold',
    render: (t) => <span className="font-mono text-caption text-navy">{t.threshold_code}</span>,
  },
  {
    key: 'pct',
    header: 'Value',
    align: 'right',
    render: (t) => <span className="font-mono text-caption">{pct(t.value_pct)}</span>,
  },
  {
    key: 'from',
    header: 'From',
    align: 'right',
    render: (t) => <span className="text-caption text-slate">{fmtDate(t.effective_from)}</span>,
  },
  {
    key: 'approved',
    header: 'Approved by',
    align: 'right',
    render: (t) => <span className="text-caption text-slate">{t.approved_by || DASH}</span>,
  },
];

const keyColumns: Column<TenantIntegrationKey>[] = [
  {
    key: 'label',
    header: 'Label',
    sortable: true,
    sortAccessor: (k) => k.label,
    render: (k) => <span className="text-body text-navy">{k.label}</span>,
  },
  {
    key: 'prefix',
    header: 'Key',
    render: (k) => <span className="font-mono text-caption text-slate">{k.key_prefix}…</span>,
  },
  {
    key: 'status',
    header: 'Status',
    sortable: true,
    sortAccessor: (k) => k.status,
    render: (k) => <StatusChip value={k.status} />,
  },
  {
    key: 'last_used',
    header: 'Last used',
    align: 'right',
    sortable: true,
    sortAccessor: (k) => k.last_used_at ?? '',
    render: (k) =>
      k.last_used_at ? (
        <span className="text-caption text-slate" title={fmtTs(k.last_used_at)}>
          {relTime(k.last_used_at)}
        </span>
      ) : (
        <span className="text-slate-light">{DASH}</span>
      ),
  },
  {
    key: 'created',
    header: 'Created',
    align: 'right',
    sortable: true,
    sortAccessor: (k) => k.created_at,
    render: (k) => (
      <span className="text-caption text-slate" title={fmtTs(k.created_at)}>
        {relTime(k.created_at)}
      </span>
    ),
  },
];

export function TenantConfigSection({
  data,
  loading,
  error,
  reload,
  orgId,
  orgLabel,
}: {
  data: TenantConfigResponse | null;
  loading: boolean;
  error: ApiError | null;
  reload: () => void;
  orgId: string;
  orgLabel?: string;
}) {
  const [mapping, setMapping] = useState<TenantMappingConfig | null>(null);

  const banks = data?.banks ?? [];
  const mappings = data?.active_mappings ?? [];
  const liquidity = data?.threshold_register?.liquidity ?? [];
  const capital = data?.threshold_register?.capital ?? [];
  const sso = data?.sso ?? null;
  const keys = data?.integration_keys ?? [];

  const mappingColumns: Column<TenantMappingConfig>[] = [
    {
      key: 'name',
      header: 'Mapping',
      sortable: true,
      sortAccessor: (m) => m.name,
      render: (m) => (
        <div className="min-w-0">
          <div className="text-body text-navy">{m.name}</div>
          <div className="font-mono text-micro text-slate">
            {m.source_system} · {m.source_ref}
          </div>
        </div>
      ),
    },
    {
      key: 'version',
      header: 'Version',
      align: 'right',
      sortable: true,
      sortAccessor: (m) => m.version,
      render: (m) => <span className="font-mono text-caption">v{m.version}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortAccessor: (m) => m.status,
      render: (m) => <StatusChip value={m.status} />,
    },
    {
      key: 'config',
      header: 'Config',
      align: 'right',
      render: (m) => (
        <span className="text-caption text-slate">
          {Object.keys(m.config ?? {}).length} key(s)
        </span>
      ),
    },
  ];

  return (
    <SectionCard
      title="Configuration"
      subtitle="Non-secret diagnostics — no credentials, secrets, or key material"
      noPadding
      className="mb-5"
    >
      <DeepSectionBody
        loading={loading && !data}
        error={error}
        reload={reload}
        context="Loading configuration"
        orgId={orgId}
        orgLabel={orgLabel}
        showEmpty={false}
        empty={null}
      >
        <div>
          <Block title={`Banks (${banks.length})`}>
            {banks.length > 0 ? (
              <DataTable columns={bankColumns} rows={banks} density="compact" />
            ) : (
              <p className="px-4 py-3 text-caption text-slate">No banks provisioned.</p>
            )}
          </Block>

          <Block title={`Active mappings (${mappings.length})`} hint="Open a row to view the raw mapping config.">
            {mappings.length > 0 ? (
              <DataTable
                columns={mappingColumns}
                rows={mappings}
                density="compact"
                initialSort={{ key: 'name', dir: 'asc' }}
                onRowClick={(m) => setMapping(m)}
              />
            ) : (
              <p className="px-4 py-3 text-caption text-slate">No active source mappings.</p>
            )}
          </Block>

          <Block title="Threshold register" hint="Current Board-approved internal thresholds (effective, open-ended).">
            <div className="grid gap-4 px-4 py-3 lg:grid-cols-2">
              <div>
                <div className="mb-1 text-micro uppercase tracking-wider text-slate">Liquidity</div>
                {liquidity.length > 0 ? (
                  <DataTable columns={liquidityColumns} rows={liquidity} density="compact" />
                ) : (
                  <p className="text-caption text-slate">No liquidity thresholds.</p>
                )}
              </div>
              <div>
                <div className="mb-1 text-micro uppercase tracking-wider text-slate">Capital</div>
                {capital.length > 0 ? (
                  <DataTable columns={capitalColumns} rows={capital} density="compact" />
                ) : (
                  <p className="text-caption text-slate">No capital thresholds.</p>
                )}
              </div>
            </div>
          </Block>

          <Block title="SSO connection">
            {sso ? (
              <div className="grid gap-x-8 px-4 py-3 sm:grid-cols-2">
                <FieldRow label="Issuer">
                  <span className="break-all font-mono text-caption">{sso.issuer}</span>
                </FieldRow>
                <FieldRow label="Client ID">
                  <span className="break-all font-mono text-caption">{sso.client_id}</span>
                </FieldRow>
                <FieldRow label="Allowed domains">
                  {sso.allowed_email_domains.length > 0
                    ? sso.allowed_email_domains.join(', ')
                    : DASH}
                </FieldRow>
                <FieldRow label="State">
                  <span className="flex flex-wrap gap-1.5">
                    <Chip tone={sso.enabled ? 'ok' : 'neutral'}>
                      {sso.enabled ? 'enabled' : 'disabled'}
                    </Chip>
                    <Chip tone={sso.jit_enabled ? 'accent' : 'neutral'}>
                      JIT {sso.jit_enabled ? 'on' : 'off'}
                    </Chip>
                    <Chip tone={sso.secret_configured ? 'ok' : 'warn'}>
                      secret {sso.secret_configured ? 'configured' : 'missing'}
                    </Chip>
                  </span>
                </FieldRow>
              </div>
            ) : (
              <p className="px-4 py-3 text-caption text-slate">No SSO connection configured.</p>
            )}
          </Block>

          <Block title={`Integration keys (${keys.length})`}>
            {keys.length > 0 ? (
              <DataTable
                columns={keyColumns}
                rows={keys}
                density="compact"
                initialSort={{ key: 'created', dir: 'desc' }}
              />
            ) : (
              <p className="px-4 py-3 text-caption text-slate">No integration keys issued.</p>
            )}
          </Block>
        </div>
      </DeepSectionBody>

      <Drawer
        open={Boolean(mapping)}
        onClose={() => setMapping(null)}
        title={mapping?.name ?? 'Mapping'}
        description={mapping ? `${mapping.source_system} · ${mapping.source_ref} · v${mapping.version}` : undefined}
        width="w-[560px]"
      >
        {mapping && (
          <pre className="max-h-[70vh] overflow-auto rounded border border-border-light bg-surface p-3 font-mono text-caption leading-relaxed text-navy/90">
            {formatJson(mapping.config)}
          </pre>
        )}
      </Drawer>
    </SectionCard>
  );
}
