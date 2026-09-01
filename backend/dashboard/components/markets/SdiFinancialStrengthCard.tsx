'use client';

/**
 * The advisory SDI financial-strength assessment (`AEQ-GH-SDI-FS`).
 *
 * A SEPARATE card from `ImpliedRatingCard` on purpose. That one renders the
 * universal-bank scorecard — grade, PIT/TTC probability of default, sovereign
 * ceiling — and an SDI has none of those: `AEQ-GH-SDI-FS` v1 releases component
 * scores and nothing else (dossier §4 keeps the grade and PD states closed until
 * back-testing evidence exists). Pointing the bank card at an SDI payload would
 * render an empty grade block and read as a broken rating rather than a
 * deliberate scope.
 *
 * Everything shown is carried on the live metric: the component scores, which
 * components were omitted and why, the methodology version, and the limitations.
 * Nothing is computed here — a UI that recomputed a score could disagree with
 * the assessment that was actually recorded.
 */

import type { LiveModuleView } from '@aequoros/risk-service-api';
import { fmtDateUTC } from '@/lib/api/values';

type ComponentScore = {
  code: string;
  score: string;
  weight: string;
  contribution: string;
};

const COMPONENT_LABELS: Record<string, string> = {
  capital_resilience: 'Capital resilience',
  asset_quality: 'Asset quality',
  liquidity_resilience: 'Liquidity resilience',
  concentration: 'Concentration',
  earnings_capacity: 'Earnings capacity',
  irrbb_sensitivity: 'Interest-rate sensitivity',
};

/** Score bands are presentational only — they are NOT a grade. */
function barTone(score: number): string {
  if (score >= 0.66) return 'bg-success';
  if (score >= 0.33) return 'bg-warning';
  return 'bg-critical';
}

export default function SdiFinancialStrengthCard({
  rating,
}: {
  rating: LiveModuleView;
}) {
  const metrics = rating.metrics as Record<string, unknown>;
  const components = Array.isArray(metrics.component_scores)
    ? (metrics.component_scores as ComponentScore[])
    : [];
  if (components.length === 0) return null;

  const omitted = Array.isArray(metrics.omitted_components)
    ? (metrics.omitted_components as string[])
    : [];
  const limitations = (
    Array.isArray(metrics.limitations) ? (metrics.limitations as string[]) : []
  ).filter(
    // The card states the advisory scope itself (footer, first line); the
    // backend ships the same sentence inside `limitations`, and printing it
    // twice reads as boilerplate rather than a warning.
    (limitation) => !limitation.toLowerCase().includes('not an agency rating')
  );
  const version = metrics.methodology_version;
  const grade =
    typeof metrics.rating_grade === 'string' ? metrics.rating_grade : undefined;
  const standalone =
    typeof metrics.standalone_grade === 'string' ? metrics.standalone_grade : undefined;
  const ceiling =
    typeof metrics.sovereign_ceiling === 'string' ? metrics.sovereign_ceiling : undefined;
  const ceilingApplied = metrics.ceiling_applied === true;
  const composite = components.reduce(
    (total, component) => total + Number(component.contribution),
    0
  );

  return (
    <div className="border border-border bg-surface-raised rounded-lg overflow-hidden">
      <div className="px-5 py-4 border-b border-border flex items-start justify-between gap-4 flex-wrap">
        <div>
          <p className="text-body font-medium text-navy">
            SDI financial strength — advisory
          </p>
          <p className="mt-1 text-caption text-slate">
            {String(metrics.methodology_code ?? 'AEQ-GH-SDI-FS')}
            {version ? ` v${String(version)}` : ''} · as of{' '}
            {fmtDateUTC(rating.sourceAsOfDate)}
          </p>
        </div>
        <div className="flex items-start gap-6">
          {grade ? (
            <div className="text-right">
              <p className="text-caption text-slate">Internal grade</p>
              <p className="text-h2 font-mono uppercase text-navy leading-none mt-1">
                {grade}
              </p>
              {ceilingApplied && ceiling ? (
                // The ceiling is the difference between what the scorecard
                // measured and what is issued; hiding it would present a capped
                // grade as a standalone judgement.
                <p className="mt-1 text-micro text-slate">
                  capped at sovereign {ceiling}
                  {standalone ? ` · standalone ${standalone}` : ''}
                </p>
              ) : null}
            </div>
          ) : null}
          <div className="text-right">
            <p className="text-caption text-slate">Composite</p>
            <p className="text-h3 font-mono tnum text-navy">
              {composite.toFixed(4)}
            </p>
          </div>
        </div>
      </div>

      <div className="px-5 py-4 space-y-3">
        {components.map((component) => {
          const score = Number(component.score);
          return (
            <div key={component.code} className="flex items-center gap-3">
              <span className="w-48 shrink-0 text-caption text-navy/85">
                {COMPONENT_LABELS[component.code] ?? component.code}
              </span>
              <span className="flex-1 h-2 rounded bg-surface overflow-hidden">
                <span
                  className={`block h-full ${barTone(score)}`}
                  style={{ width: `${Math.round(score * 100)}%` }}
                />
              </span>
              <span className="w-16 text-right font-mono text-caption tnum text-navy">
                {score.toFixed(4)}
              </span>
              <span className="w-20 text-right font-mono text-micro tnum text-slate">
                w {Number(component.weight).toFixed(3)}
              </span>
            </div>
          );
        })}
      </div>

      <div className="px-5 py-3 border-t border-border bg-surface space-y-1.5">
        {/* The scope statement is not a disclaimer to skim — it is what stops the
            reader treating a component score as an agency rating. */}
        <p className="text-micro text-slate leading-relaxed">
          Advisory internal grade and component scores — not an agency rating,
          not a regulatory filing input, and not a probability of default.
        </p>
        {omitted.length > 0 ? (
          <p className="text-micro text-slate leading-relaxed">
            Omitted (no usable input, never scored at a neutral value):{' '}
            {omitted
              .map((code) => COMPONENT_LABELS[code] ?? code)
              .join(', ')}
            . Remaining weights are renormalised.
          </p>
        ) : null}
        {limitations.map((limitation) => (
          <p key={limitation} className="text-micro text-slate leading-relaxed">
            {limitation}
          </p>
        ))}
      </div>
    </div>
  );
}
