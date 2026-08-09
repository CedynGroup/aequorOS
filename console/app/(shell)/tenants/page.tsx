'use client';

import { useRouter } from 'next/navigation';
import { listTenants } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, DASH } from '@/lib/format';
import {
  Chip,
  EmptyState,
  ErrorPanel,
  FreshnessChip,
  IngestionChip,
  PageHeader,
  SkeletonRows,
  SsoChip,
} from '@/components/ui';
import Link from 'next/link';

/**
 * /tenants — the institution inventory.
 * Source: GET /operator/v1/tenants (every column comes from that payload;
 * missing optional fields render as an em dash, never a fabricated value).
 */
export default function TenantsPage() {
  const router = useRouter();
  const { data, error, loading, reload } = useApi(listTenants);

  return (
    <div>
      <PageHeader
        title="Tenants"
        sub="Every onboarded institution on this deployment, straight from the operator API."
      />

      <div className="card overflow-x-auto">
        {loading && <SkeletonRows rows={6} />}

        {error && (
          <div className="p-4">
            <ErrorPanel error={error} onRetry={reload} context="Loading tenants" />
          </div>
        )}

        {data && data.tenants.length === 0 && (
          <EmptyState
            title="No tenants on this deployment yet"
            hint={
              <>
                Provision the first institution from{' '}
                <Link href="/onboard" className="text-action hover:underline">
                  Onboard
                </Link>
                . A bank goes live on its first ingestion.
              </>
            }
          />
        )}

        {data && data.tenants.length > 0 && (
          <table className="w-full text-body">
            <thead>
              <tr className="border-b border-border-light bg-surface text-left text-micro uppercase tracking-wide text-slate">
                <th className="px-4 py-2.5 font-medium">Bank</th>
                <th className="px-4 py-2.5 font-medium">Organization</th>
                <th className="px-4 py-2.5 font-medium">Jurisdiction</th>
                <th className="px-4 py-2.5 font-medium">Currency</th>
                <th className="px-4 py-2.5 font-medium">License</th>
                <th className="px-4 py-2.5 font-medium">Periods</th>
                <th className="px-4 py-2.5 font-medium">Freshness</th>
                <th className="px-4 py-2.5 font-medium">Last batch</th>
                <th className="px-4 py-2.5 font-medium">Storage</th>
                <th className="px-4 py-2.5 font-medium">SSO</th>
              </tr>
            </thead>
            <tbody>
              {data.tenants.map((t) => (
                <tr
                  key={t.organization_id}
                  onClick={() => router.push(`/tenants/${t.organization_id}`)}
                  className="cursor-pointer border-b border-border-light last:border-b-0 hover:bg-surface"
                >
                  <td className="px-4 py-2.5">
                    <Link
                      href={`/tenants/${t.organization_id}`}
                      className="font-medium text-navy hover:text-action"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {t.bank_name ?? 'No bank yet'}
                    </Link>
                    <div className="font-mono text-caption text-slate">{t.bank_id ?? DASH}</div>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="text-ink">{t.organization_name}</div>
                    <div className="font-mono text-caption text-slate">{t.organization_id}</div>
                  </td>
                  <td className="px-4 py-2.5 text-ink">{t.jurisdiction_code ?? DASH}</td>
                  <td className="px-4 py-2.5 font-mono text-caption text-ink">
                    {t.currency ?? DASH}
                  </td>
                  <td className="px-4 py-2.5 text-ink">{t.license_type ?? DASH}</td>
                  <td className="px-4 py-2.5">
                    <span className="num text-ink">{t.period_count}</span>
                    <div className="text-caption text-slate">
                      latest {fmtDate(t.latest_period_end)}
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    <FreshnessChip summary={t.freshness} />
                  </td>
                  <td className="px-4 py-2.5">
                    <IngestionChip summary={t.last_ingestion} />
                  </td>
                  <td className="px-4 py-2.5">
                    {t.storage_provider ? <Chip mono>{t.storage_provider}</Chip> : DASH}
                  </td>
                  <td className="px-4 py-2.5">
                    <SsoChip configured={t.sso_configured} enabled={t.sso_enabled} />
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
