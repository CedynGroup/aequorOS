'use client';

/**
 * Methodology transparency drawer (FC-5 deliverable §4 — the bank's model-risk
 * evidence). Read-only. It is deliberately honest about provenance: it separates
 * what the bank-facing views payload actually carries (identity + §15
 * arbitration + §11.4 freshness) from the display conventions THIS view applies
 * when deriving the forward grid, and from the desk methodology fields that are
 * not carried on this surface yet (instruments, interpolation method, native
 * conventions, calendar, methodology version). Nothing here is fabricated: a
 * field the payload does not carry is shown as an explicit gap, not a guess.
 */

import { X } from 'lucide-react';
import type { YieldCurveViewRead } from '@aequoros/risk-service-api';
import { fmtDateUTC, fmtTimestamp, shortId } from '@/lib/api/values';
import { ANCHOR_BASIS, BASIS_LABELS } from '@/lib/markets/curveGrid';
import AttributionChip from './AttributionChip';
import { CurveTypeBadge, MonoChip, SyntheticProxyBadge } from './chips';

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5">
      <span className="text-caption text-slate whitespace-nowrap">{label}</span>
      <span className="text-caption text-navy text-right min-w-0">{children}</span>
    </div>
  );
}

function GapRow({ label, detail }: { label: string; detail: string }) {
  return (
    <li className="flex items-start justify-between gap-3 py-1.5">
      <div className="min-w-0">
        <p className="text-caption text-navy">{label}</p>
        <p className="text-micro text-slate">{detail}</p>
      </div>
      <span className="shrink-0 inline-flex items-center px-1.5 py-0.5 rounded border border-border-light bg-surface text-micro font-medium uppercase tracking-wider text-slate whitespace-nowrap">
        Not in payload
      </span>
    </li>
  );
}

export default function MethodologyDrawer({
  curve,
  onClose,
}: {
  curve: YieldCurveViewRead;
  onClose: () => void;
}) {
  return (
    <>
      <div className="fixed inset-0 bg-navy/30 z-40" onClick={onClose} aria-hidden />
      <aside
        className="fixed inset-y-0 right-0 z-50 w-full max-w-lg bg-surface-raised border-l border-border shadow-xl overflow-y-auto"
        role="dialog"
        aria-label={`Methodology for ${curve.curveName}`}
      >
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border-light sticky top-0 bg-surface-raised z-10">
          <div className="min-w-0 space-y-1">
            <h2 className="text-h2 text-navy">Methodology &amp; conventions</h2>
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="font-medium text-navy">{curve.currency}</span>
              <MonoChip>{curve.curveName}</MonoChip>
              <CurveTypeBadge curveType={curve.curveType} />
              {curve.curveType === 'discount' && <SyntheticProxyBadge />}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="p-1.5 rounded text-slate hover:text-navy hover:bg-surface"
          >
            <X size={16} aria-hidden />
          </button>
        </div>

        <div className="px-5 py-4 space-y-6">
          {/* What the payload carries. */}
          <section className="space-y-1">
            <h3 className="text-body font-semibold text-navy">Published &amp; attributed</h3>
            <p className="text-caption text-slate">
              Carried by the bank-facing views payload for this curve.
            </p>
            <div className="mt-2 divide-y divide-border-light">
              <Field label="Curve code">
                <MonoChip>{curve.curveName}</MonoChip>
              </Field>
              <Field label="Currency">{curve.currency}</Field>
              <Field label="Curve family">
                <CurveTypeBadge curveType={curve.curveType} />
              </Field>
              <Field label="Published as-of">
                <span className="font-mono">{fmtDateUTC(curve.asOfDate)}</span>
              </Field>
              <Field label="Source system">
                <AttributionChip attribution={curve.attribution} />
              </Field>
              <Field label="Ingestion batch">
                <span
                  className="font-mono"
                  title={curve.attribution.ingestionBatchId}
                >
                  {shortId(curve.attribution.ingestionBatchId, 12)}…
                </span>
              </Field>
              <Field label="Ingested at">
                <span className="font-mono">{fmtTimestamp(curve.attribution.ingestedAt)}</span>
              </Field>
              <Field label="Tenor points">{curve.points.length}</Field>
            </div>
          </section>

          {/* Conventions THIS view applies to derive the grid. */}
          <section className="space-y-1">
            <h3 className="text-body font-semibold text-navy">Display conventions applied here</h3>
            <p className="text-caption text-slate">
              How this view derives the forward grid from the published points — plainly, so the
              numbers are reproducible.
            </p>
            <ul className="mt-2 space-y-2 text-caption text-navy">
              <li>
                <span className="font-medium">Discount factor.</span> Simple money-market form
                DF = 1 / (1 + rate·τ), computed on the {BASIS_LABELS[ANCHOR_BASIS]} anchor and held
                invariant across the &ldquo;Convert to&rdquo; switch.
              </li>
              <li>
                <span className="font-medium">Day-count.</span> {BASIS_LABELS.act360} by default;{' '}
                {BASIS_LABELS.act365} and {BASIS_LABELS.actact} (ISDA) offered as output bases —
                these re-express the displayed yield only, never the DF.
              </li>
              <li>
                <span className="font-medium">Period dates.</span> Start/End rolled from the
                published as-of anchor by whole months (day clamped to month-end). These are
                derived, not the desk&rsquo;s exact schedule dates.
              </li>
              <li>
                <span className="font-medium">Interpolation.</span> None applied — the published
                tenor points are rendered as-is; no intermediate tenors are interpolated
                client-side.
              </li>
            </ul>
          </section>

          {/* The model-risk fields not yet on this surface — honest gaps. */}
          <section className="space-y-1">
            <h3 className="text-body font-semibold text-navy">Not carried on this surface yet</h3>
            <p className="text-caption text-slate">
              The desk&rsquo;s full model-risk record. These are not in the bank-facing views
              payload; a dedicated methodology read endpoint would be needed to expose them
              verbatim rather than derive or assume.
            </p>
            <ul className="mt-2 divide-y divide-border-light">
              <GapRow
                label="Instruments &amp; market inputs"
                detail="The quotes/instruments the curve was constructed from."
              />
              <GapRow
                label="Interpolation method"
                detail="The desk's between-tenor interpolation (e.g. log-linear on DF, monotone)."
              />
              <GapRow
                label="Native day-count &amp; business-day conventions"
                detail="The basis the published rate is actually quoted on."
              />
              <GapRow
                label="Holiday calendar"
                detail="The calendar governing settlement and schedule dates."
              />
              <GapRow
                label="Methodology version &amp; effective date"
                detail="The versioned methodology the published curve was built under."
              />
            </ul>
          </section>

          <p className="text-caption text-slate border-t border-border-light pt-3">
            The published golden copy is never modified here. Every figure above is either carried
            verbatim in the attributed payload or derived by the plainly-stated conventions in this
            view.
          </p>
        </div>
      </aside>
    </>
  );
}
