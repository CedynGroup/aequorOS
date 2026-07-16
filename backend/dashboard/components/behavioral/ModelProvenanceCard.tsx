import { Sparkles } from 'lucide-react';
import type { BehavioralModelRead } from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import { fmtDateUTC } from '@/lib/api/values';

/**
 * SR 11-7 style model provenance: identifier, version, method, training
 * window and holdout accuracy — every field straight off the behavioral
 * model payload.
 */
export default function ModelProvenanceCard({
  result,
}: {
  result: BehavioralModelRead;
}) {
  const { accuracy } = result;
  return (
    <SectionCard
      title={
        <span className="inline-flex items-center gap-2">
          <Sparkles size={15} className="text-action" aria-hidden />
          Model provenance
        </span>
      }
      subtitle="Recorded with every applied assumption batch (SR 11-7)"
      actions={
        <StatusPill tone={result.method === 'ml' ? 'compliant' : 'pending'}>
          {result.method === 'ml' ? 'ML estimator' : 'Baseline heuristic'}
        </StatusPill>
      }
    >
      <dl className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-x-6 gap-y-4">
        <Field label="Model ID">
          <span className="font-mono text-caption text-navy break-all">
            {result.modelId}
          </span>
        </Field>
        <Field label="Version">
          <span className="font-mono text-caption text-navy">{result.modelVersion}</span>
        </Field>
        <Field label="Trained as of">
          <span className="font-mono text-caption tnum text-navy">
            {result.asOfDate ? fmtDateUTC(result.asOfDate) : '—'}
          </span>
        </Field>
        <Field label="Training sample">
          <span className="font-mono text-caption tnum text-navy">
            {accuracy.sampleCount.toLocaleString('en-US')} rows
          </span>
        </Field>
        <Field label="History coverage">
          <span className="font-mono text-caption tnum text-navy">
            {accuracy.monthCoverage} months
          </span>
        </Field>
        <Field label="Holdout CV error">
          <span className="font-mono text-caption tnum text-navy">
            {accuracy.cvRmse != null ? `RMSE ${accuracy.cvRmse.toFixed(3)}` : 'not cross-validated'}
            {accuracy.cvMae != null && (
              <span className="text-slate"> · MAE {accuracy.cvMae.toFixed(3)}</span>
            )}
          </span>
        </Field>
      </dl>
    </SectionCard>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-micro uppercase tracking-wider text-slate">{label}</dt>
      <dd className="mt-1">{children}</dd>
    </div>
  );
}
