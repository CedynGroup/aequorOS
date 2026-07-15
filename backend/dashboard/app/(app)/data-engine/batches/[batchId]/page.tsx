'use client';

/**
 * Immutable detail of one ingestion batch: outcome, record gating counts,
 * the operator validation report, untranslatable rows (with their raw source
 * preserved), and where the raw file and report landed in tiered storage.
 */

import PageHeader from '@/components/ui/PageHeader';
import EmptyState from '@/components/ui/EmptyState';
import { ErrorPanel, PageSkeleton } from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useIngestionBatch,
  useTranslationFailures,
} from '@/lib/api/ingestion';
import {
  ArtifactPath,
  BatchStatusPill,
  CountStrip,
  batchBlockerDetails,
  formatDate,
  formatDateTime,
  referenceRowCounts,
} from '@/components/data-engine/shared';

type ReportFailure = {
  rule: string;
  severity: string;
  entity_type: string | null;
  source_reference: string | null;
  source_locator: string | null;
  detail: string;
};

const SEVERITY_TONE: Record<string, string> = {
  BLOCKER: 'text-critical',
  ERROR: 'text-critical',
  WARNING: 'text-warning',
  INFO: 'text-slate',
};

export default function BatchDetailPage({
  params,
}: {
  params: { batchId: string };
}) {
  const { bank } = useBankContext();
  const batchQuery = useIngestionBatch(bank?.id, params.batchId);
  const failuresQuery = useTranslationFailures(bank?.id, params.batchId);

  if (batchQuery.isPending) {
    return (
      <div className="px-8 py-6">
        <PageSkeleton />
      </div>
    );
  }
  if (batchQuery.isError) {
    return (
      <div className="px-8 py-6">
        <ErrorPanel
          error={batchQuery.error}
          onRetry={() => void batchQuery.refetch()}
          title="Could not load the batch"
        />
      </div>
    );
  }

  const batch = batchQuery.data;
  const report = batch.validationReport as {
    summary?: Record<string, unknown>;
    failures?: ReportFailure[];
    reconciliation?: Record<string, unknown>;
    suppressed_findings?: Record<string, number>;
  };
  const findings = report.failures ?? [];
  const translationFailures = failuresQuery.data?.failures ?? [];
  const blockerDetails = batchBlockerDetails(batch);
  const referenceCounts = referenceRowCounts(batch);
  const suppressed = report.suppressed_findings ?? {};

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Data Engine', href: '/data-engine' },
          { label: 'Batches' },
          { label: batch.id.slice(0, 8) },
        ]}
        title={
          <span className="flex items-center gap-3">
            Ingestion batch{' '}
            <span className="font-mono text-h2 text-slate">{batch.id.slice(0, 13)}</span>
            <BatchStatusPill status={batch.status} />
          </span>
        }
        subtitle={`${batch.sourceSystem} · as of ${formatDate(batch.asOfDate)} · started ${formatDateTime(batch.startedAt ?? batch.createdAt)}`}
      />
      <div className="px-8 py-6 space-y-6 max-w-6xl">
        <CountStrip batch={batch} />

        {batch.status === 'rejected' && (
          <div className="card border-l-4 border-l-critical p-5 space-y-2">
            <p className="text-body font-medium text-critical">
              Batch rejected — nothing from this source was accepted
            </p>
            {(blockerDetails.length > 0
              ? blockerDetails
              : ['A blocking validation failure rejected the batch.']
            ).map((detail, index) => (
              <p key={index} className="text-body text-navy/80">
                {detail}
              </p>
            ))}
          </div>
        )}

        {batch.errorMessage && (
          <div className="card border-l-4 border-l-critical p-5">
            <p className="text-body font-medium text-navy">
              {batch.errorCode ?? 'failure'}
            </p>
            <p className="mt-1 text-body text-navy/80">{batch.errorMessage}</p>
          </div>
        )}

        {Object.keys(referenceCounts).length > 0 && (
          <div className="card p-5">
            <h2 className="text-h3 text-navy">Reference datasets</h2>
            <p className="mt-1 text-caption text-slate">
              Rows preserved verbatim per dataset kind for the calculation
              modules (curves, capital, behavioral assumptions, history).
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {Object.entries(referenceCounts).map(([kind, count]) => (
                <span
                  key={kind}
                  className="inline-flex items-center gap-2 rounded border border-border px-2.5 py-1 text-caption font-mono text-navy"
                >
                  {kind}
                  <span className="text-slate">{count}</span>
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="card p-5 space-y-2">
          <h2 className="text-h3 text-navy">Stored artifacts</h2>
          <p className="text-caption text-slate">
            Immutable, versioned, and encrypted in the institution&apos;s
            dedicated storage buckets.
          </p>
          <ArtifactPath label="Raw source" path={batch.rawArtifactPath} />
          <ArtifactPath label="Validation report" path={batch.reportArtifactPath} />
          {batch.contentHash && (
            <ArtifactPath label="Content SHA-256" path={batch.contentHash} />
          )}
        </div>

        <section className="space-y-3">
          <h2 className="text-h2 text-navy">Validation findings</h2>
          {Object.keys(suppressed).length > 0 && (
            <p className="text-caption text-slate">
              Large batch:{' '}
              {Object.entries(suppressed)
                .map(([rule, count]) => `${count} further ${rule} findings`)
                .join(', ')}{' '}
              are counted in the totals above but not listed individually.
            </p>
          )}
          {findings.length === 0 ? (
            <EmptyState
              title="No validation findings"
              description="Every record in this batch passed the configured rules."
            />
          ) : (
            <div className="card divide-y divide-border-light">
              {findings.map((finding, index) => (
                <div key={index} className="px-5 py-3 flex items-start gap-4">
                  <span
                    className={`shrink-0 w-20 text-caption font-medium ${SEVERITY_TONE[finding.severity] ?? 'text-slate'}`}
                  >
                    {finding.severity}
                  </span>
                  <div className="min-w-0">
                    <p className="text-body text-navy">{finding.detail}</p>
                    <p className="mt-0.5 text-caption font-mono text-slate truncate">
                      {finding.rule}
                      {finding.source_reference ? ` · ${finding.source_reference}` : ''}
                      {finding.source_locator ? ` · ${finding.source_locator}` : ''}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="space-y-3">
          <h2 className="text-h2 text-navy">Untranslatable rows</h2>
          {translationFailures.length === 0 ? (
            <p className="text-body text-slate">
              Every extracted row translated into the canonical model.
            </p>
          ) : (
            <div className="card divide-y divide-border-light">
              {translationFailures.map((failure) => (
                <div key={failure.id} className="px-5 py-3">
                  <div className="flex items-center gap-3">
                    <span className="text-caption font-medium text-critical">
                      {failure.errorCode}
                    </span>
                    <span className="text-caption font-mono text-slate truncate">
                      {failure.sourceLocator}
                    </span>
                  </div>
                  <p className="mt-1 text-body text-navy">{failure.errorMessage}</p>
                  <pre className="mt-2 rounded bg-surface px-3 py-2 text-caption font-mono text-slate overflow-x-auto">
                    {JSON.stringify(failure.rawRecord)}
                  </pre>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </>
  );
}
