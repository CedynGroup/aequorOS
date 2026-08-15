'use client';

import { useMemo, useState, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button, Field, FormError, Input, Select, StatusPill, Textarea } from '@/components/ui';
import type {
  DeskCurveDefinition,
  DeskCurveDefinitionFields,
  DeskCurveKind,
} from '@/lib/api';

/**
 * Shared bits for the desk Curve Construction workspace (FC-4, spec §4.1).
 *
 * The option vocabularies below MIRROR the backend's governed resolvers
 * (app/services/market_desk/curve_construction.py + app/domain/curves/*): the
 * seeded holiday calendars, the four interpolators, the DayCount enum, the
 * BusinessDayConvention enum, and the two extrapolation rules. A value the
 * backend cannot resolve is a 422 on save, so we constrain the pickers to
 * exactly what it accepts rather than inventing options.
 */

// ---------------------------------------------------------------------------
// Governed option vocabularies (mirrored from the backend resolvers)
// ---------------------------------------------------------------------------

/**
 * The seeded holiday calendars. SINGLE SOURCE OF TRUTH — the definition form
 * and the FX-forward grid both import this so a new calendar is added in one
 * place, not diverged per page.
 */
export const CALENDAR_OPTIONS = ['GHANA', 'USA', 'NIGERIA', 'KENYA', 'SOUTH_AFRICA'] as const;

export const CURVE_KIND_OPTIONS: { value: DeskCurveKind; label: string }[] = [
  { value: 'forward', label: 'Forward' },
  { value: 'zero', label: 'Zero' },
  { value: 'discount', label: 'Discount' },
];

export const INTERPOLATION_OPTIONS: { value: string; label: string }[] = [
  { value: 'monotone_convex', label: 'Hagan–West monotone convex' },
  { value: 'log_linear_df', label: 'Log-linear on discount factors' },
  { value: 'pchip', label: 'PCHIP (piecewise cubic Hermite)' },
  { value: 'linear_zero', label: 'Linear on zero rates' },
];

/** Output basis — the Eikon "Convert to" contract (DayCount.value strings). */
export const DAYCOUNT_OPTIONS: { value: string; label: string }[] = [
  { value: 'ACT/360', label: 'Actual/360 — money-market' },
  { value: 'ACT/365F', label: 'Actual/365 Fixed' },
  { value: 'ACT/364', label: 'Actual/364 — GFIM standard' },
  { value: '30/360', label: '30/360 — bond' },
  { value: 'ACT/ACT', label: 'Actual/Actual — ISDA' },
];

export const ROLL_CONVENTION_OPTIONS: { value: string; label: string }[] = [
  { value: 'modified_following', label: 'Modified following' },
  { value: 'following', label: 'Following' },
  { value: 'preceding', label: 'Preceding' },
  { value: 'modified_preceding', label: 'Modified preceding' },
  { value: 'unadjusted', label: 'Unadjusted' },
];

export const EXTRAPOLATION_OPTIONS: { value: string; label: string }[] = [
  { value: 'flat_forward', label: 'Flat forward (hold terminal forward)' },
  { value: 'flat_zero', label: 'Flat zero (hold terminal zero)' },
];

/** FC-6d distribution tier — an org sees a published curve only at/below its own tier. */
export const ENTITLEMENT_TIER_OPTIONS: {
  value: 'core' | 'standard' | 'premium';
  label: string;
}[] = [
  { value: 'core', label: 'Core — rates + FX' },
  { value: 'standard', label: 'Standard — + sovereign & discount curves' },
  { value: 'premium', label: 'Premium — + credit curves' },
];

// ---------------------------------------------------------------------------
// Status + ceremony
// ---------------------------------------------------------------------------

/** Curve-definition lifecycle pill: draft ▸ approved. */
export function CurveDefinitionStatusPill({ status }: { status: string }) {
  if (status === 'approved') return <StatusPill tone="success">approved</StatusPill>;
  if (status === 'draft') return <StatusPill tone="pending">draft</StatusPill>;
  return <StatusPill tone="amber">{status.replace(/_/g, ' ')}</StatusPill>;
}

/**
 * The Track-2 amber ceremony banner — the visual weight that separates a
 * governed definition change (or a golden-copy publish) from a routine run.
 * Same treatment as the methodology register (spec §11a).
 */
export function CeremonyBanner({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
      <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warning" />
      <div className="min-w-0 text-caption text-slate">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Active-definition resolution (mirrors get_active_definition server-side)
// ---------------------------------------------------------------------------

/**
 * The version construction will apply: the latest APPROVED version whose
 * effective_from is on or before the as-of date. ISO date strings compare
 * lexicographically, so a plain string compare is correct here.
 */
export function activeDefinitionFor(
  definitions: DeskCurveDefinition[],
  asOf: string,
): DeskCurveDefinition | null {
  const eligible = definitions
    .filter((d) => d.status === 'approved' && d.effective_from !== null && d.effective_from <= asOf)
    .sort((a, b) => b.version - a.version);
  return eligible[0] ?? null;
}

// ---------------------------------------------------------------------------
// The governed definition form (create + propose)
// ---------------------------------------------------------------------------

export interface DefinitionFormValue extends DeskCurveDefinitionFields {
  curve_code: string;
}

/**
 * The parameter surface of a curve definition, as a structured form (never a
 * raw JSON blob — every knob is a governed methodology parameter). Used for
 * both "New definition" (mode=create, curve_code editable) and "Propose new
 * version" (mode=propose, prefilled from the base version).
 */
export function DefinitionForm({
  mode,
  base,
  busy,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  mode: 'create' | 'propose';
  /** Prefill source: an existing version (propose) or undefined (create). */
  base?: DeskCurveDefinition;
  busy: boolean;
  submitLabel: string;
  onSubmit: (fields: DeskCurveDefinitionFields, curveCode: string) => void;
  onCancel: () => void;
}) {
  const [curveCode, setCurveCode] = useState(base?.curve_code ?? '');
  const [currency, setCurrency] = useState(base?.currency ?? '');
  const [calendar, setCalendar] = useState(base?.calendar_name ?? 'GHANA');
  const [kind, setKind] = useState<DeskCurveKind>((base?.curve_kind as DeskCurveKind) ?? 'forward');
  const [interpolation, setInterpolation] = useState(
    base?.interpolation_method ?? 'monotone_convex',
  );
  const [outputDaycount, setOutputDaycount] = useState(base?.output_daycount ?? 'ACT/360');
  const [roll, setRoll] = useState(base?.roll_convention ?? 'modified_following');
  const [extrapolation, setExtrapolation] = useState(base?.extrapolation_rule ?? 'flat_forward');
  const [entitlementTier, setEntitlementTier] = useState<'core' | 'standard' | 'premium'>(
    (base?.entitlement_tier as 'core' | 'standard' | 'premium') ?? 'standard',
  );
  const [curveFrequency, setCurveFrequency] = useState(base?.curve_frequency ?? '3M');
  const [paymentIntervalMonths, setPaymentIntervalMonths] = useState(
    String(base?.payment_interval_months ?? 3),
  );
  const [paymentFrequency, setPaymentFrequency] = useState(base?.payment_frequency ?? '');
  const [spotLagDays, setSpotLagDays] = useState(String(base?.spot_lag_days ?? 2));
  const [projectionIndex, setProjectionIndex] = useState(base?.projection_index ?? '');
  const [discountCurveCode, setDiscountCurveCode] = useState(base?.discount_curve_code ?? '');
  const [instrumentSetRef, setInstrumentSetRef] = useState(base?.instrument_set_ref ?? '');
  const [paramsText, setParamsText] = useState(
    JSON.stringify(base?.params ?? {}, null, 2),
  );
  const [rationale, setRationale] = useState('');

  const paramsParse = useMemo(():
    | { ok: true; value: Record<string, unknown> }
    | { ok: false; error: string } => {
    const text = paramsText.trim();
    if (text === '') return { ok: true, value: {} };
    try {
      const parsed: unknown = JSON.parse(text);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { ok: false, error: 'Params must be a JSON object.' };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : 'Invalid JSON.' };
    }
  }, [paramsText]);

  const interval = Number(paymentIntervalMonths);
  const lag = Number(spotLagDays);
  const currencyOk = /^[A-Za-z]{3}$/.test(currency.trim());
  const valid =
    (mode === 'propose' || curveCode.trim() !== '') &&
    currencyOk &&
    instrumentSetRef.trim() !== '' &&
    curveFrequency.trim() !== '' &&
    Number.isInteger(interval) &&
    interval > 0 &&
    Number.isInteger(lag) &&
    lag >= 0 &&
    rationale.trim() !== '' &&
    paramsParse.ok;

  function submit() {
    if (!valid || !paramsParse.ok) return;
    onSubmit(
      {
        currency: currency.trim().toUpperCase(),
        calendar_name: calendar,
        curve_kind: kind,
        projection_index: projectionIndex.trim() || null,
        discount_curve_code: discountCurveCode.trim() || null,
        instrument_set_ref: instrumentSetRef.trim(),
        interpolation_method: interpolation,
        output_daycount: outputDaycount,
        payment_frequency: paymentFrequency.trim() || null,
        payment_interval_months: interval,
        curve_frequency: curveFrequency.trim(),
        spot_lag_days: lag,
        roll_convention: roll,
        extrapolation_rule: extrapolation,
        params: paramsParse.value,
        entitlement_tier: entitlementTier,
        change_rationale: rationale.trim(),
      },
      curveCode.trim(),
    );
  }

  const intervalInvalid = paymentIntervalMonths.trim() !== '' && !(Number.isInteger(interval) && interval > 0);
  const lagInvalid = spotLagDays.trim() !== '' && !(Number.isInteger(lag) && lag >= 0);

  return (
    <div className="space-y-4">
      <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
        {mode === 'create' && (
          <Field
            label="Curve code"
            required
            hint="AEQ.* golden-copy name — immutable once created"
          >
            <Input
              className="font-mono"
              value={curveCode}
              onChange={(e) => setCurveCode(e.target.value)}
              placeholder="AEQ.GHS.SOV.FWD"
              spellCheck={false}
            />
          </Field>
        )}
        <Field
          label="Currency"
          required
          hint="ISO-4217 (3 letters)"
          error={currency.trim() !== '' && !currencyOk ? 'Must be exactly three letters.' : undefined}
        >
          <Input
            className="font-mono uppercase"
            value={currency}
            maxLength={3}
            invalid={currency.trim() !== '' && !currencyOk}
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
            placeholder="GHS"
          />
        </Field>
        <Field label="Calendar">
          <Select value={calendar} onChange={(e) => setCalendar(e.target.value)}>
            {CALENDAR_OPTIONS.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Curve kind">
          <Select value={kind} onChange={(e) => setKind(e.target.value as DeskCurveKind)}>
            {CURVE_KIND_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Projection index" hint="Forward Curve — e.g. GHS-SOV, SOFR (optional)">
          <Input
            className="font-mono"
            value={projectionIndex ?? ''}
            onChange={(e) => setProjectionIndex(e.target.value)}
            placeholder="GHS-SOV"
            spellCheck={false}
          />
        </Field>
        <Field label="Discount curve" hint="OIS / AGD code (optional — self-discounting if unset)">
          <Input
            className="font-mono"
            value={discountCurveCode ?? ''}
            onChange={(e) => setDiscountCurveCode(e.target.value)}
            placeholder="AEQ.GHS.OIS"
            spellCheck={false}
          />
        </Field>
        <Field label="Instrument set" required hint="Governed 'Swap style' identifier">
          <Input
            className="font-mono"
            value={instrumentSetRef}
            onChange={(e) => setInstrumentSetRef(e.target.value)}
            placeholder="GHS.SOV.BILLS_BONDS"
            spellCheck={false}
          />
        </Field>
        <Field label="Interpolation">
          <Select value={interpolation} onChange={(e) => setInterpolation(e.target.value)}>
            {INTERPOLATION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Output basis (Convert to)">
          <Select value={outputDaycount} onChange={(e) => setOutputDaycount(e.target.value)}>
            {DAYCOUNT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Curve frequency" required hint="Forward-grid period length, e.g. 3M / 1M">
          <Input
            className="font-mono"
            value={curveFrequency}
            onChange={(e) => setCurveFrequency(e.target.value)}
            placeholder="3M"
          />
        </Field>
        <Field
          label="Payment interval (months)"
          error={intervalInvalid ? 'Must be a positive whole number.' : undefined}
        >
          <Input
            type="number"
            min={1}
            className="font-mono"
            invalid={intervalInvalid}
            value={paymentIntervalMonths}
            onChange={(e) => setPaymentIntervalMonths(e.target.value)}
          />
        </Field>
        <Field label="Payment frequency" hint="Descriptive label (optional)">
          <Input
            value={paymentFrequency ?? ''}
            onChange={(e) => setPaymentFrequency(e.target.value)}
            placeholder="quarterly"
          />
        </Field>
        <Field
          label="Spot lag (days)"
          error={lagInvalid ? 'Must be zero or a positive whole number.' : undefined}
        >
          <Input
            type="number"
            min={0}
            className="font-mono"
            invalid={lagInvalid}
            value={spotLagDays}
            onChange={(e) => setSpotLagDays(e.target.value)}
          />
        </Field>
        <Field label="Roll convention">
          <Select value={roll} onChange={(e) => setRoll(e.target.value)}>
            {ROLL_CONVENTION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Extrapolation">
          <Select value={extrapolation} onChange={(e) => setExtrapolation(e.target.value)}>
            {EXTRAPOLATION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Entitlement tier"
          hint="Distribution tier a tenant needs to receive this curve"
        >
          <Select
            value={entitlementTier}
            onChange={(e) => setEntitlementTier(e.target.value as 'core' | 'standard' | 'premium')}
          >
            {ENTITLEMENT_TIER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      <details className="rounded border border-border-light bg-surface">
        <summary className="cursor-pointer px-3 py-2 text-caption font-medium text-slate">
          Advanced params (JSON — QA tolerances, grid overrides)
        </summary>
        <div className="px-3 pb-3">
          <Textarea
            rows={6}
            spellCheck={false}
            className="font-mono text-caption"
            invalid={!paramsParse.ok}
            value={paramsText}
            onChange={(e) => setParamsText(e.target.value)}
          />
          {!paramsParse.ok && <FormError>JSON error: {paramsParse.error}</FormError>}
          <p className="mt-1 text-micro text-slate-light">
            Optional overrides: grid_periods, qa_grid_points, oscillation_tolerance,
            pillar_min_count, enforce_positive_forwards, end_of_month.
          </p>
        </div>
      </details>

      <Field
        label="Change rationale (required — recorded in the register)"
        required
      >
        <Textarea
          rows={2}
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          placeholder="Why this definition / this change? Cite the methodology basis."
        />
      </Field>

      <div className="flex gap-2">
        <Button loading={busy} disabled={!valid} onClick={submit}>
          {submitLabel}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline SVG line chart (dependency-free spark)
// ---------------------------------------------------------------------------

export interface ChartPoint {
  x: number;
  y: number;
}

/**
 * A small, dependency-free single-series SVG line chart, retained for trivial
 * inline sparks. The interactive workspace/FX visuals use recharts through the
 * ChartFrame primitive (see components/curves/CurveResultCharts + FxForwardChart);
 * this stays for lightweight, no-tooltip spark cases. Theme-driven through the
 * --chart-* CSS tokens.
 */
export function LineChart({
  points,
  color = 'var(--chart-1)',
  height = 220,
  yFormat = (v) => v.toFixed(2),
  xFormat = (v) => String(v),
  yTicks = 4,
  ariaLabel,
}: {
  points: ChartPoint[];
  color?: string;
  height?: number;
  yFormat?: (v: number) => string;
  xFormat?: (v: number) => string;
  yTicks?: number;
  ariaLabel?: string;
}) {
  const W = 680;
  const H = height;
  const padL = 60;
  const padR = 18;
  const padT = 14;
  const padB = 30;

  if (points.length === 0) {
    return <div className="p-6 text-center text-caption text-slate">No data to plot.</div>;
  }

  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  let minX = Math.min(...xs);
  let maxX = Math.max(...xs);
  let minY = Math.min(...ys);
  let maxY = Math.max(...ys);
  if (minX === maxX) {
    minX -= 1;
    maxX += 1;
  }
  if (minY === maxY) {
    const pad = Math.abs(minY) > 0 ? Math.abs(minY) * 0.05 : 0.5;
    minY -= pad;
    maxY += pad;
  } else {
    const margin = (maxY - minY) * 0.08;
    minY -= margin;
    maxY += margin;
  }

  const sx = (x: number) => padL + ((x - minX) / (maxX - minX)) * (W - padL - padR);
  const sy = (y: number) => padT + (1 - (y - minY) / (maxY - minY)) * (H - padT - padB);

  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`).join(' ');
  const gridYs = Array.from({ length: yTicks + 1 }, (_, i) => minY + ((maxY - minY) * i) / yTicks);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      role="img"
      aria-label={ariaLabel}
      className="block"
      preserveAspectRatio="xMidYMid meet"
    >
      {/* horizontal gridlines + y labels */}
      {gridYs.map((gy, i) => (
        <g key={i}>
          <line
            x1={padL}
            x2={W - padR}
            y1={sy(gy)}
            y2={sy(gy)}
            stroke="var(--chart-grid)"
            strokeWidth={1}
          />
          <text
            x={padL - 8}
            y={sy(gy) + 3}
            textAnchor="end"
            fontSize={10}
            fill="rgb(var(--text-faint))"
            fontFamily="var(--font-plex-mono), monospace"
          >
            {yFormat(gy)}
          </text>
        </g>
      ))}
      {/* x axis baseline */}
      <line
        x1={padL}
        x2={W - padR}
        y1={H - padB}
        y2={H - padB}
        stroke="rgb(var(--line-strong))"
        strokeWidth={1}
      />
      {/* x labels: first + last */}
      <text
        x={padL}
        y={H - padB + 16}
        textAnchor="start"
        fontSize={10}
        fill="rgb(var(--text-faint))"
        fontFamily="var(--font-plex-mono), monospace"
      >
        {xFormat(points[0].x)}
      </text>
      <text
        x={W - padR}
        y={H - padB + 16}
        textAnchor="end"
        fontSize={10}
        fill="rgb(var(--text-faint))"
        fontFamily="var(--font-plex-mono), monospace"
      >
        {xFormat(points[points.length - 1].x)}
      </text>
      {/* series */}
      <path d={path} fill="none" stroke={color} strokeWidth={1.75} strokeLinejoin="round" />
      {points.map((p, i) => (
        <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r={2.4} fill={color} />
      ))}
    </svg>
  );
}
