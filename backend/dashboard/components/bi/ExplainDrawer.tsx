"use client";

/**
 * Where one figure came from.
 *
 * Provenance is a property of a figure IN CONTEXT, so the drawer asks about the
 * measure AND the query it was read in: the same measure at another date, or
 * answered from the daily aggregate rather than the fact grain, has a different
 * answer. `POST …/bi/explain` returns no SQL — the statement encodes the mart
 * layout and is not a bank-facing surface — so what a reviewer gets is the
 * measure's own declaration, the table and window it was read over, the engine
 * row it is a copy of when it is one, and the current status of every
 * reconciliation check that governs it.
 *
 * Absent evidence is shown as absent. A blank input hash means the figure is
 * not an engine copy, not that it has one and we did not look.
 */

import { useEffect } from "react";
import { X } from "lucide-react";
import type { BiQuery, BiTrustCheckRead } from "@aequoros/risk-service-api";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { useBiExplain, isBiAccessDenied } from "@/lib/api/bi";
import RestrictedWidget from "./RestrictedWidget";
import TrustBadge from "./TrustBadge";
import { NOT_MEASURED } from "./result";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="text-caption text-slate">{label}</dt>
      <dd className="min-w-0 text-right text-caption text-navy break-words">
        {value}
      </dd>
    </div>
  );
}

function text(value: string | null | undefined): string {
  return value && value.length > 0 ? value : NOT_MEASURED;
}

function CheckRow({ check }: { check: BiTrustCheckRead }) {
  return (
    <li className="flex items-start justify-between gap-3 border-t border-border-light py-2 first:border-t-0">
      <div className="min-w-0">
        <p className="text-caption font-medium text-navy">{check.label}</p>
        {check.difference && check.tolerance && (
          <p className="mt-0.5 text-micro text-slate">
            Difference {check.difference} against a tolerance of{" "}
            {check.tolerance}
          </p>
        )}
      </div>
      <TrustBadge status={check.status} size="compact" />
    </li>
  );
}

export default function ExplainDrawer({
  bankId,
  measure,
  query,
  onClose,
}: {
  bankId: string | undefined;
  /** The catalogue measure id being explained, or null when closed. */
  measure: string | null;
  /** The query the figure was read in. */
  query: BiQuery | null;
  onClose: () => void;
}) {
  const open = measure !== null && query !== null;
  const explain = useBiExplain(bankId, measure, query, open);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const data = explain.data;
  const engine = data?.engine;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Where this figure came from"
      className="fixed inset-0 z-50 flex justify-end"
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
      />
      <aside className="relative h-full w-full max-w-md overflow-y-auto border-l border-border bg-surface-raised shadow-pop">
        <header className="sticky top-0 flex items-start justify-between gap-3 border-b border-border-light bg-surface-raised px-5 py-4">
          <div className="min-w-0">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Where this figure came from
            </p>
            <h2 className="mt-1 text-h3 text-navy">
              {data?.measure.label ?? measure}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-md border border-border p-1.5 text-slate hover:bg-surface"
          >
            <X size={14} aria-hidden />
          </button>
        </header>

        <div className="space-y-5 px-5 py-4">
          {explain.isPending && (
            <div className="space-y-2" aria-busy="true">
              <SkeletonLine width="80%" />
              <SkeletonLine width="60%" />
              <SkeletonLine width="70%" />
            </div>
          )}

          {isBiAccessDenied(explain.error) && <RestrictedWidget />}

          {explain.error && !isBiAccessDenied(explain.error) && (
            <ErrorPanel
              error={explain.error}
              onRetry={() => void explain.refetch()}
              title="Could not load this figure's provenance"
            />
          )}

          {data && (
            <>
              <section>
                <p className="text-body leading-relaxed text-navy/85">
                  {data.measure.description}
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <TrustBadge
                    status={data.trust?.status}
                    failingChecks={data.trust?.failingChecks ?? []}
                    size="compact"
                  />
                  {data.measure.certified && (
                    <span className="rounded border border-success/20 bg-success-light px-1.5 py-0.5 text-micro font-medium uppercase tracking-wider text-success">
                      Filed figure
                    </span>
                  )}
                  {data.measure.advisoryDesignation && (
                    <span className="rounded border border-border bg-surface px-1.5 py-0.5 text-micro font-medium uppercase tracking-wider text-slate">
                      {data.measure.advisoryDesignation.replace(/_/g, " ")}
                    </span>
                  )}
                </div>
              </section>

              <section>
                <h3 className="mb-1 text-caption font-semibold text-navy">
                  How it was read
                </h3>
                <dl className="divide-y divide-border-light">
                  <Row label="Source table" value={text(data.sourceTable)} />
                  <Row
                    label="Answered from"
                    value={
                      data.usedAggregate
                        ? "The daily aggregate"
                        : "The fact grain"
                    }
                  />
                  <Row label="Aggregation" value={data.measure.aggregation} />
                  <Row label="Currency conversion" value={text(data.fxRule)} />
                  <Row
                    label="Window"
                    value={
                      data.asOf
                        ? text(data.asOf)
                        : `${text(data.windowStart)} to ${text(data.windowEnd)}`
                    }
                  />
                  {data.compareTo && (
                    <Row label="Compared with" value={text(data.compareTo)} />
                  )}
                  <Row
                    label="Catalogue version"
                    value={data.catalogueVersion}
                  />
                  <Row label="Mart build" value={text(data.buildFingerprint)} />
                </dl>
              </section>

              {data.components && data.components.length > 0 && (
                <section>
                  <h3 className="mb-1 text-caption font-semibold text-navy">
                    What it is composed from
                  </h3>
                  <ul className="space-y-1">
                    {data.components.map((component) => (
                      <li
                        key={`${component.role}:${component.memberId}`}
                        className="flex items-baseline justify-between gap-3 text-caption"
                      >
                        <span className="text-slate">
                          {component.role.replace(/_/g, " ")}
                        </span>
                        <span className="text-navy">{component.label}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {engine && (
                <section>
                  <h3 className="mb-1 text-caption font-semibold text-navy">
                    The engine row this is a copy of
                  </h3>
                  <dl className="divide-y divide-border-light">
                    <Row label="Metric" value={engine.metricId} />
                    <Row label="Module" value={engine.module} />
                    <Row label="Tier" value={engine.tier} />
                    <Row label="Basis" value={text(engine.regime)} />
                    <Row label="Reporting date" value={text(engine.asOf)} />
                    <Row
                      label="Figure"
                      value={
                        engine.value
                          ? `${engine.value}${engine.unit ? ` ${engine.unit}` : ""}`
                          : NOT_MEASURED
                      }
                    />
                    <Row label="Input hash" value={text(engine.inputHash)} />
                    <Row
                      label="Engine version"
                      value={text(engine.engineVersion)}
                    />
                    <Row label="Computed" value={text(engine.computedAt)} />
                    {engine.reconciliationBlocked === true && (
                      <Row
                        label="Reconciliation"
                        value="Blocked when this figure was computed"
                      />
                    )}
                  </dl>
                </section>
              )}

              <section>
                <h3 className="mb-1 text-caption font-semibold text-navy">
                  Reconciliation checks that govern it
                </h3>
                {data.checks && data.checks.length > 0 ? (
                  <ul>
                    {data.checks.map((check) => (
                      <CheckRow key={check.checkId} check={check} />
                    ))}
                  </ul>
                ) : (
                  <p className="text-caption leading-relaxed text-slate">
                    No reconciliation check governs this measure, so its trust
                    state is reported as not assessed.
                  </p>
                )}
              </section>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
