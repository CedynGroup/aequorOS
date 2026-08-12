'use client';

import { Chip, StatusChip, type Tone } from '@/components/ui';
import { fmtTs, relTime, DASH } from '@/lib/format';
import type { DeskDetermination, DeskPublication } from '@/lib/api';

// --------------------------------------------------------------------------
// Markets Desk shared bits: the determination state vocabulary and the QA
// verdict, rendered identically on the list, detail, and publications pages.
// --------------------------------------------------------------------------

/**
 * Fixed tones for the determinations state machine
 * (services/market_desk/determinations.py): draft -> pending_review ->
 * approved -> published, with rejected as the checker's branch. An unknown
 * state falls back to the generic keyword-inferred StatusChip.
 */
const DETERMINATION_TONES: Record<string, Tone> = {
  draft: 'neutral',
  pending_review: 'warn',
  approved: 'ok',
  published: 'accent',
  rejected: 'crit',
};

export function DeterminationStatusPill({ status }: { status: string }) {
  const tone = DETERMINATION_TONES[status];
  if (!tone) return <StatusChip value={status} />;
  return <Chip tone={tone}>{status.replace(/_/g, ' ')}</Chip>;
}

/**
 * Rates package readiness (gates approve/submit). Curves QA is shown
 * separately — a curves fail does not block rates publish.
 */
export function QaBadge({ determination }: { determination: DeskDetermination }) {
  const derived = determination.derived_values ?? {};
  const rates =
    derived.rates_qa_passed !== undefined
      ? derived.rates_qa_passed
      : derived.qa_passed;
  if (rates === true) return <Chip tone="ok">rates ready</Chip>;
  if (rates === false) return <Chip tone="crit">rates fail</Chip>;
  return <Chip>not computed</Chip>;
}

/** Curves QA badge — advisory for rates-first publish. */
export function CurvesQaBadge({ determination }: { determination: DeskDetermination }) {
  const curves = determination.derived_values?.curves_qa_passed;
  if (curves === true) return <Chip tone="ok">curves pass</Chip>;
  if (curves === false) return <Chip tone="warn">curves fail</Chip>;
  return null;
}

/** Methodology binding as a mono chip: CODE vN. */
export function MethodologyChip({ code, version }: { code: string; version: number }) {
  return (
    <Chip mono title={`Methodology ${code} version ${version}`}>
      {code} v{version}
    </Chip>
  );
}

/**
 * One publication's header line + per-bank fan-out results, straight from
 * DeskPublication.results (services/market_desk/publication.py). Partial
 * failure is the contract there — per-bank errors are recorded, not rolled
 * back — so the table renders failed banks alongside completed ones.
 */
export function PublicationResults({ publication }: { publication: DeskPublication }) {
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 text-caption text-slate">
        <StatusChip value={publication.status} />
        <span>
          published by <span className="font-mono text-ink">{publication.published_by}</span>
        </span>
        <span title={fmtTs(publication.published_at)}>{relTime(publication.published_at)}</span>
      </div>
      {publication.results.length === 0 ? (
        <p className="mt-2 text-caption text-slate">
          No per-bank results recorded — there were no banks to publish to.
        </p>
      ) : (
        <table className="mt-2 w-full text-body">
          <thead>
            <tr className="border-b border-border-light text-left text-micro uppercase tracking-wide text-slate">
              <th className="py-1.5 pr-4 font-medium">Bank</th>
              <th className="py-1.5 pr-4 font-medium">Ingestion batch</th>
              <th className="py-1.5 pr-4 font-medium">Status</th>
              <th className="py-1.5 font-medium">Error</th>
            </tr>
          </thead>
          <tbody>
            {publication.results.map((r, i) => (
              <tr
                key={`${r.bank_id ?? i}`}
                className="border-b border-border-light last:border-b-0"
              >
                <td className="py-1.5 pr-4 font-mono text-caption text-ink">
                  {r.bank_id ?? DASH}
                </td>
                <td className="py-1.5 pr-4 font-mono text-caption text-slate">
                  {r.ingestion_batch_id ?? DASH}
                </td>
                <td className="py-1.5 pr-4">
                  <StatusChip value={r.status} />
                </td>
                <td className="py-1.5 text-caption text-critical">{r.error ?? DASH}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
