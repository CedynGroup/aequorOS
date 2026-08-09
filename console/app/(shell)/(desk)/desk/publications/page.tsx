'use client';

import Link from 'next/link';
import { listDeskPublications } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { PublicationResults } from '@/components/desk';
import { EmptyState, ErrorPanel, PageHeader, SkeletonRows } from '@/components/ui';

/**
 * /desk/publications — every fan-out the desk has run, newest first.
 * Source: GET /operator/v1/desk/publications. Each row links back to the
 * determination it published and renders its per-bank delivery results
 * verbatim (partial failure is recorded, never hidden).
 */
export default function PublicationsPage() {
  const { data, error, loading, reload } = useApi(() => listDeskPublications());

  return (
    <div>
      <PageHeader
        title="Publications"
        sub="Deliberate, logged fan-outs of approved determinations into every bank's canonical store — one ingestion batch per bank, superseding by natural key."
      />

      <div className="card">
        {loading && <SkeletonRows rows={5} />}

        {error && (
          <div className="p-4">
            <ErrorPanel error={error} onRetry={reload} context="Loading publications" />
          </div>
        )}

        {data && data.publications.length === 0 && (
          <EmptyState
            title="Nothing published yet"
            hint="Approve a determination on the Determinations screen and publish it — the fan-out record appears here."
          />
        )}

        {data && data.publications.length > 0 && (
          <ul className="divide-y divide-border-light">
            {data.publications.map((p) => (
              <li key={p.id} className="px-5 py-4">
                <div className="mb-2">
                  <Link
                    href={`/desk/determinations/${p.determination_id}`}
                    className="font-medium text-navy hover:text-action"
                  >
                    Determination{' '}
                    <span className="font-mono text-caption">{p.determination_id}</span>
                  </Link>
                </div>
                <PublicationResults publication={p} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
