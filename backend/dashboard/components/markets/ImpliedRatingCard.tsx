import { useId, type ReactNode } from "react";
import { Info, TrendingDown, TrendingUp } from "lucide-react";
import type { LiveModuleView } from "@aequoros/risk-service-api";
import StatusPill from "@/components/ui/StatusPill";
import { fmtTimestamp, num } from "@/lib/api/values";

/** A metric value is "present" when the backend actually emitted it. */
function present(value: string | undefined | null): value is string {
  return value !== undefined && value !== null && value !== "";
}

function formatPercent(value: string | number, dp = 2): string {
  return `${num(value).toFixed(dp)}%`;
}

/** Grades arrive lower-case (e.g. "bb−"); render them like a rating scale. */
function fmtGrade(value: string | undefined): string {
  return present(value) ? value.toUpperCase() : "—";
}

/**
 * Green→red PD risk-gradient encoding. These are fixed data-encoding colors
 * (low PD = green, high PD = red), NOT theme tokens — kept literal on purpose
 * so the scale reads identically in both light and dark themes.
 */
const PD_GRADIENT =
  "linear-gradient(to right,#16a34a 0%,#65a30d 22%,#eab308 42%,#f97316 66%,#dc2626 100%)";

/** Fixed 0–60% PD scale → position (0–100) as a percent of track width. */
function pct(value: number): number {
  return (Math.max(0, Math.min(60, value)) / 60) * 100;
}

type Placement = "bottom-start" | "bottom-end" | "top-end";

const PLACEMENT: Record<Placement, string> = {
  "bottom-start": "top-full left-0 mt-2",
  "bottom-end": "top-full right-0 mt-2",
  "top-end": "bottom-full right-0 mb-2",
};

/**
 * Hover/focus explainer, matching the shell's `role="tooltip"` pattern
 * (dark chip on the always-dark nav surface). Text-only — the trigger is a
 * keyboard-focusable info affordance so the copy is reachable without a mouse.
 */
function InfoTip({
  label,
  placement = "bottom-start",
  width = "w-72",
  children,
}: {
  label: string;
  placement?: Placement;
  width?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <span className="relative inline-flex group/tip align-middle">
      <button
        type="button"
        aria-label={label}
        aria-describedby={id}
        className="inline-flex items-center justify-center text-slate hover:text-action focus-visible:text-action transition-colors"
      >
        <Info size={12} aria-hidden />
      </button>
      <span
        role="tooltip"
        id={id}
        className={`pointer-events-none absolute z-50 ${width} rounded border border-white/15 bg-nav px-3 py-2 text-caption font-normal normal-case leading-relaxed tracking-normal text-white/90 opacity-0 shadow-pop transition-opacity duration-150 group-hover/tip:opacity-100 group-focus-within/tip:opacity-100 ${PLACEMENT[placement]}`}
      >
        {children}
      </span>
    </span>
  );
}

type Band = {
  key: string;
  lower: number;
  point: number;
  upper: number;
  /** Systematic factor (Z) — PIT only. */
  z?: number | null;
};

/**
 * One PD band as a horizontal risk-gradient track on the fixed 0–60% scale.
 *  · gradient  — green (low PD) → red (high PD), literal data-encoding colors
 *  · band      — outlined window from pct(lower) to pct(upper) in the primary
 *                foreground token (theme-aware) with a subtle dark ring
 *  · point     — 2px white tick at pct(point), slightly taller than the track
 */
function PdTrack({ band }: { band: Band }) {
  const lo = pct(band.lower);
  const up = pct(band.upper);
  const pt = pct(band.point);
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-micro tnum text-slate">
          <span className="font-semibold text-navy">{band.key}</span> · point{" "}
          <span className="font-mono tnum text-navy">
            {band.point.toFixed(2)}%
          </span>
        </span>
        {band.z !== null && band.z !== undefined && (
          <span className="text-micro tnum text-slate">
            Z{" "}
            <span className="font-mono tnum text-navy">
              {band.z.toFixed(2)}
            </span>
          </span>
        )}
      </div>

      <div
        className="relative h-3.5 rounded"
        style={{ background: PD_GRADIENT }}
        aria-hidden
      >
        {/* band window (lower→upper) */}
        <div
          className="absolute inset-y-0 rounded-sm border-2 border-navy"
          style={{
            left: `${lo}%`,
            width: `${Math.max(0, up - lo)}%`,
            boxShadow: "0 0 0 2px rgba(0,0,0,.35)",
          }}
        />
        {/* point estimate — white tick, slightly taller than the track */}
        <div
          className="absolute -top-0.5 -bottom-0.5 w-0.5 -translate-x-1/2 bg-white"
          style={{ left: `${pt}%` }}
        />
      </div>

      <div className="mt-1 flex items-baseline justify-between text-micro tnum text-slate">
        <span>
          <span className="font-sans text-slate-light">lower </span>
          {band.lower.toFixed(2)}%
        </span>
        <span>
          {band.upper.toFixed(2)}%
          <span className="font-sans text-slate-light"> upper</span>
        </span>
      </div>
    </div>
  );
}

function DriverChip({
  tone,
  prefix,
  label,
}: {
  tone: "up" | "down";
  prefix: string;
  label: string;
}) {
  const styles =
    tone === "up"
      ? "bg-success-light text-success border-success/20"
      : "bg-critical-light text-critical border-critical/20";
  const Icon = tone === "up" ? TrendingUp : TrendingDown;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-caption font-medium ${styles}`}
    >
      <Icon size={12} aria-hidden />
      {prefix}
      <span className="font-normal text-navy/80">{label}</span>
    </span>
  );
}

/** Bank's current live Treasury/ALM credit assessment, distinct from agency observations. */
export default function ImpliedRatingCard({
  rating,
}: {
  rating: LiveModuleView;
}) {
  const metrics = rating.metrics as Record<string, string>;
  if (metrics.availability === "unavailable") return null;

  // Build the PD tracks only from complete lower·point·upper triples.
  const bands: Band[] = [];
  if (
    present(metrics.pit_pd_lower_pct) &&
    present(metrics.pit_pd_point_pct) &&
    present(metrics.pit_pd_upper_pct)
  ) {
    bands.push({
      key: "PIT",
      lower: num(metrics.pit_pd_lower_pct),
      point: num(metrics.pit_pd_point_pct),
      upper: num(metrics.pit_pd_upper_pct),
      z: present(metrics.pit_systematic_factor)
        ? num(metrics.pit_systematic_factor)
        : null,
    });
  }
  if (
    present(metrics.ttc_pd_lower_pct) &&
    present(metrics.ttc_pd_point_pct) &&
    present(metrics.ttc_pd_upper_pct)
  ) {
    bands.push({
      key: "TTC",
      lower: num(metrics.ttc_pd_lower_pct),
      point: num(metrics.ttc_pd_point_pct),
      upper: num(metrics.ttc_pd_upper_pct),
    });
  }

  const confidencePct = present(metrics.confidence_level)
    ? `${(num(metrics.confidence_level) * 100).toFixed(0)}%`
    : null;
  const ceilingApplied = metrics.ceiling_applied === "true";
  const ddepEligible = metrics.ddep_eligible === "true";
  const hasUp = present(metrics.key_driver_up);
  const hasDown = present(metrics.key_driver_down);

  const disclaimerTip =
    "AequorOS's implied credit assessment of your institution, derived only from your reported " +
    "financial statements using a documented, transparent methodology modeled on the frameworks " +
    "published by Moody’s, S&P, and Fitch. It is an indicative internal assessment to help you " +
    "benchmark and monitor your standing — not a credit rating issued by a licensed rating " +
    "agency, and not investment advice.";

  const pdBandTip =
    "We show a range, not a single number, on purpose. Bank defaults are rare in this market, so " +
    "there is very little historical default data to calibrate an exact probability against. " +
    "Rather than imply a precision we don’t have, we publish a conservative range: the upper " +
    `figure${present(metrics.pit_pd_upper_pct) ? ` (${formatPercent(metrics.pit_pd_upper_pct)})` : ""} ` +
    `is a deliberately cautious estimate${confidencePct ? ` at a ${confidencePct} confidence level` : ""}, ` +
    "with an added margin for data limitations. Decisions that put capital at risk use the upper, " +
    "most conservative figure.";

  const sovereignTip =
    "A bank’s assessment is anchored to the strength of the country it operates in. Because the " +
    "domestic sovereign’s own credit standing constrains what any bank operating there can be " +
    "assessed at, no bank is rated above the sovereign except in narrowly defined cases. This " +
    "reflects a real risk demonstrated by the domestic debt restructuring (DDEP), which imposed " +
    "large losses on banks holding government securities.";

  const confidenceTip =
    "The width of the range reflects how much data supports the estimate. A wider band means more " +
    "uncertainty — usually from limited default history in this market. As more data " +
    "accumulates over time, we expect these ranges to narrow. We would rather show an honest wide " +
    "range than a precise number we cannot defend.";

  const methodologyTip = (
    <span className="block space-y-1.5">
      <span className="block">
        Built only from your reported financials, using a documented method modeled
        on the S&amp;P, Moody’s and Fitch frameworks:
      </span>
      <span className="block space-y-1">
        <span className="block">
          <span className="font-semibold text-white">Scorecard</span> — capital, asset
          quality, earnings, funding &amp; liquidity are scored on the agency factor
          framework to place a through-the-cycle (TTC) grade.
        </span>
        <span className="block">
          <span className="font-semibold text-white">Master scale</span> — each grade maps
          to an idealised one-year default rate (agency-aligned): the TTC anchor PD.
        </span>
        <span className="block">
          <span className="font-semibold text-white">Point-in-time</span> — the anchor is
          conditioned on the live operating-environment factor (Z) through a Vasicek
          single-factor model, so a weaker environment lifts PIT above TTC.
        </span>
        <span className="block">
          <span className="font-semibold text-white">Range, not a point</span> — a Bayesian
          posterior band reflects thin local default history; a margin of conservatism sets
          the upper figure used for capital decisions.
        </span>
        <span className="block">
          <span className="font-semibold text-white">Floor &amp; ceiling</span> — no PD
          falls below the Basel 0.03% floor, and the grade is capped near the sovereign
          (a real risk shown by the DDEP).
        </span>
      </span>
      {present(metrics.methodology_version) && (
        <span className="block text-white/50">
          Active methodology · version {metrics.methodology_version}
        </span>
      )}
    </span>
  );

  return (
    <div className="border border-border bg-surface-raised rounded-lg">
      <div className="grid grid-cols-1 lg:grid-cols-[11rem_minmax(0,1fr)]">
        {/* Grade panel */}
        <div className="px-4 py-3 border-b lg:border-b-0 lg:border-r border-border bg-surface/50 rounded-t-lg lg:rounded-tr-none">
          <p className="inline-flex items-center gap-1.5 text-micro font-medium uppercase tracking-wider text-slate">
            Indicative internal assessment
            <InfoTip label="About this indicative assessment">
              {disclaimerTip}
            </InfoTip>
          </p>
          <div className="mt-1.5 flex items-end gap-2">
            <span className="font-mono text-h1 tnum text-navy leading-none">
              {fmtGrade(metrics.pit_rating_grade)}
            </span>
            <span className="pb-0.5 text-caption text-slate">
              PIT grade{ceilingApplied ? " · sovereign-capped" : ""}
            </span>
          </div>
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-caption text-slate">
            {present(metrics.ttc_rating_grade) && (
              <span>
                TTC{" "}
                <span className="font-mono tnum text-navy">
                  {fmtGrade(metrics.ttc_rating_grade)}
                </span>
              </span>
            )}
            {present(metrics.standalone_grade) && (
              <span>
                Standalone{" "}
                <span className="font-mono tnum text-navy">
                  {fmtGrade(metrics.standalone_grade)}
                </span>
              </span>
            )}
          </div>
          {present(metrics.sovereign_ceiling) && (
            <p className="mt-1.5 inline-flex items-start gap-1.5 text-caption text-slate">
              <span>
                Sovereign ceiling{" "}
                <span className="font-mono tnum text-navy">
                  {fmtGrade(metrics.sovereign_ceiling)}
                </span>
                {ceilingApplied ? " · applied" : " · not binding"}
              </span>
              <InfoTip label="How the sovereign ceiling affects this assessment">
                {sovereignTip}
              </InfoTip>
            </p>
          )}
        </div>

        {/* PD-track panel */}
        <div className="min-w-0 px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
            <p className="inline-flex items-center gap-1.5 text-micro font-medium uppercase tracking-wider text-slate">
              One-year probability of default
              <InfoTip
                label="Why we show a range, not a single number"
                placement="bottom-start"
              >
                {pdBandTip}
              </InfoTip>
            </p>
            <div className="flex items-center gap-2.5">
              <span className="inline-flex items-center gap-1 text-micro font-medium uppercase tracking-wider text-slate">
                Methodology
                <InfoTip
                  label="The methodology behind this assessment"
                  placement="bottom-end"
                  width="w-80"
                >
                  {methodologyTip}
                </InfoTip>
              </span>
              {present(metrics.ddep_eligible) && (
                <StatusPill tone={ddepEligible ? "success" : "critical"}>
                  DDEP {ddepEligible ? "eligible" : "ineligible"}
                </StatusPill>
              )}
            </div>
          </div>

          {bands.length > 0 && (
            <>
              <div className="mt-2.5 space-y-2.5">
                {bands.map((band) => (
                  <PdTrack key={band.key} band={band} />
                ))}
              </div>
              <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-micro text-slate">
                <span className="inline-flex items-center gap-1.5">
                  <span
                    className="inline-block h-2 w-8 rounded-sm"
                    style={{ background: PD_GRADIENT }}
                    aria-hidden
                  />
                  green = low PD → red = high PD (0–60%)
                </span>
                <span aria-hidden>·</span>
                <span className="inline-flex items-center gap-1">
                  <span
                    className="inline-block h-2.5 w-3.5 rounded-[1px] border border-navy"
                    style={{ boxShadow: "0 0 0 1px rgba(0,0,0,.35)" }}
                    aria-hidden
                  />
                  band
                </span>
                <span aria-hidden>·</span>
                <span className="inline-flex items-center gap-1">
                  <span
                    className="inline-flex h-2.5 w-2.5 items-center justify-center rounded-[1px]"
                    style={{ background: PD_GRADIENT }}
                    aria-hidden
                  >
                    <span className="h-3 w-0.5 bg-white" />
                  </span>
                  point
                </span>
              </p>
            </>
          )}

          {!ddepEligible &&
            present(metrics.ddep_post_stress_capital_ratio_pct) && (
              <p className="mt-2.5 rounded border border-critical/20 bg-critical-light px-3 py-1.5 text-caption text-critical">
                DDEP-ineligible: post-stress capital ratio{" "}
                <span className="font-mono tnum font-semibold">
                  {formatPercent(metrics.ddep_post_stress_capital_ratio_pct)}
                </span>{" "}
                under the sovereign-stress scenario.
              </p>
            )}

          {(hasUp || hasDown) && (
            <div className="mt-2.5 flex flex-wrap gap-2">
              {hasUp && (
                <DriverChip
                  tone="up"
                  prefix="Supports:"
                  label={metrics.key_driver_up}
                />
              )}
              {hasDown && (
                <DriverChip
                  tone="down"
                  prefix="Drags:"
                  label={metrics.key_driver_down}
                />
              )}
            </div>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2 border-t border-border bg-surface/45 rounded-b-lg text-caption text-slate">
        <span>
          Live risk calculation from current Capital, Liquidity, IRRBB, and FX
          metrics
        </span>
        <span className="inline-flex items-center gap-1.5">
          Live as of {fmtTimestamp(rating.computedAt)}
          {confidencePct ? ` at ${confidencePct} confidence` : ""}
          <InfoTip
            label="What the width of the range means"
            placement="top-end"
          >
            {confidenceTip}
          </InfoTip>
        </span>
      </div>
    </div>
  );
}
