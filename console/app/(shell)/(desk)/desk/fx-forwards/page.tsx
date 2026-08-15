'use client';

import { useEffect, useMemo, useState } from 'react';
import { Calculator } from 'lucide-react';
import {
  constructFxForward,
  listCurveDefinitions,
  listDeskDeterminations,
  toApiError,
  type ApiError,
  type FxForwardConstructResponse,
  type FxForwardRow,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate } from '@/lib/format';
import { CALENDAR_OPTIONS } from '@/components/curves';
import { FxForwardChart } from '@/components/curves/FxForwardChart';
import {
  Button,
  Chip,
  type Column,
  DataTable,
  EmptyState,
  ErrorPanel,
  Field,
  Input,
  PageHeader,
  SectionCard,
  Select,
  SkeletonRows,
} from '@/components/ui';

const DEFAULT_TENORS = '1M, 3M, 6M, 12M';

/**
 * FX-forward day counts — the three DayCount enum members the resolver accepts
 * (app/services/market_desk/fx_forward.py::_resolve_day_count matches on the
 * enum NAME). Deliberately NOT the curve DAYCOUNT_OPTIONS, whose 30/360 and
 * ACT/ACT values the FX resolver would reject.
 */
const FX_DAY_COUNT_OPTIONS: { value: string; label: string }[] = [
  { value: 'ACT_365F', label: 'Actual/365 Fixed' },
  { value: 'ACT_360', label: 'Actual/360 — money-market' },
  { value: 'ACT_364', label: 'Actual/364 — GFIM standard' },
];

function parseTenors(value: string): string[] {
  return value
    .split(',')
    .map((tenor) => tenor.trim().toUpperCase())
    .filter(Boolean);
}

/** Staff CIP preview. Publication remains deliberately governed through desk determinations. */
export default function FxForwardsPage() {
  const definitions = useApi(() => listCurveDefinitions());
  const determinations = useApi(() => listDeskDeterminations({ status: 'published' }));
  const published = useMemo(
    () =>
      (definitions.data?.definitions ?? []).filter(
        (definition) => definition.status === 'approved' && definition.curve_kind === 'discount',
      ),
    [definitions.data],
  );
  const publishedCobs = useMemo(
    () =>
      [...new Set((determinations.data?.determinations ?? []).map((row) => row.cob_date))]
        .sort()
        .reverse(),
    [determinations.data],
  );
  const [baseCcy, setBaseCcy] = useState('USD');
  const [quoteCcy, setQuoteCcy] = useState('GHS');
  const [spot, setSpot] = useState('');
  const [asOf, setAsOf] = useState('');
  const [calendar, setCalendar] = useState('GHANA');
  const [tenors, setTenors] = useState(DEFAULT_TENORS);
  const [baseCurve, setBaseCurve] = useState('');
  const [quoteCurve, setQuoteCurve] = useState('');
  const [basisBps, setBasisBps] = useState('0');
  const [dayCount, setDayCount] = useState('ACT_365F');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FxForwardConstructResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const baseCurves = published.filter((curve) => curve.currency === baseCcy);
  const quoteCurves = published.filter((curve) => curve.currency === quoteCcy);

  useEffect(() => {
    if (!asOf && publishedCobs.length > 0) setAsOf(publishedCobs[0]);
  }, [asOf, publishedCobs]);

  useEffect(() => {
    if (!baseCurves.some((curve) => curve.curve_code === baseCurve)) setBaseCurve('');
  }, [baseCurve, baseCurves]);

  useEffect(() => {
    if (!quoteCurves.some((curve) => curve.curve_code === quoteCurve)) setQuoteCurve('');
  }, [quoteCurve, quoteCurves]);

  async function construct() {
    const parsedSpot = Number(spot);
    const parsedBasis = Number(basisBps);
    if (!Number.isFinite(parsedSpot) || parsedSpot <= 0 || !baseCurve || !quoteCurve) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await constructFxForward({
          base_ccy: baseCcy,
          quote_ccy: quoteCcy,
          spot: parsedSpot,
          as_of: asOf,
          base_leg: { source: 'published', curve_code: baseCurve },
          quote_leg: { source: 'published', curve_code: quoteCurve },
          basis_bps: Number.isFinite(parsedBasis) ? parsedBasis : 0,
          day_count: dayCount,
          tenor_grid: parseTenors(tenors),
          grid_calendar: calendar,
          grid_spot_lag_days: 2,
        }),
      );
    } catch (err) {
      setError(toApiError(err));
    }
    setBusy(false);
  }

  const disabled =
    busy ||
    !asOf ||
    !baseCurve ||
    !quoteCurve ||
    !Number.isFinite(Number(spot)) ||
    Number(spot) <= 0;

  const missingCurve = baseCurves.length === 0 || quoteCurves.length === 0;

  const resultColumns: Column<FxForwardRow>[] = [
    { key: 'date', header: 'Value date', render: (r) => <span className="font-mono text-caption text-ink">{fmtDate(r.date)}</span> },
    { key: 'forward', header: 'Forward', numeric: true, render: (r) => r.forward_rate.toFixed(4) },
    {
      key: 'points',
      header: 'Points',
      numeric: true,
      render: (r) => (
        <span className="text-slate">
          {r.forward_points >= 0 ? '+' : ''}
          {r.forward_points.toFixed(4)}
        </span>
      ),
    },
    {
      key: 'df',
      header: 'DF base / quote',
      numeric: true,
      render: (r) => (
        <span className="text-caption text-slate">
          {r.df_base.toFixed(6)} / {r.df_quote.toFixed(6)}
        </span>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="FX forwards"
        sub="Covered-interest-parity outright-forward preview from two approved published curves and an FX spot. This is an auditable staff preview; publication remains a separate governed desk action."
      />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(28rem,1.1fr)]">
        <SectionCard
          title="Construction inputs"
          subtitle="Use approved, currency-matched discount curves at a published COB. A non-zero basis is an explicit assumption, not a calibrated cross-currency basis."
        >
          {definitions.loading && <SkeletonRows rows={5} />}
          {definitions.error && (
            <ErrorPanel
              error={definitions.error}
              onRetry={definitions.reload}
              context="Loading published curves"
            />
          )}
          {definitions.data && (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Base currency">
                <Input
                  value={baseCcy}
                  onChange={(e) => setBaseCcy(e.target.value.toUpperCase())}
                  maxLength={3}
                  className="font-mono uppercase"
                />
              </Field>
              <Field label="Quote currency">
                <Input
                  value={quoteCcy}
                  onChange={(e) => setQuoteCcy(e.target.value.toUpperCase())}
                  maxLength={3}
                  className="font-mono uppercase"
                />
              </Field>
              <Field label="Spot (quote per base)">
                <Input
                  value={spot}
                  onChange={(e) => setSpot(e.target.value)}
                  inputMode="decimal"
                  placeholder="e.g. 12.8500"
                  className="font-mono"
                />
              </Field>
              <Field label="Published COB">
                <Select value={asOf} onChange={(e) => setAsOf(e.target.value)}>
                  <option value="">Select published COB</option>
                  {publishedCobs.map((cob) => (
                    <option key={cob} value={cob}>
                      {fmtDate(cob)}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={`Base discount curve (${baseCcy})`}>
                <Select
                  value={baseCurve}
                  onChange={(e) => setBaseCurve(e.target.value)}
                  className="font-mono"
                >
                  <option value="">Select {baseCcy} discount curve</option>
                  {baseCurves.map((curve) => (
                    <option key={curve.id} value={curve.curve_code}>
                      {curve.curve_code} · v{curve.version}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={`Quote discount curve (${quoteCcy})`}>
                <Select
                  value={quoteCurve}
                  onChange={(e) => setQuoteCurve(e.target.value)}
                  className="font-mono"
                >
                  <option value="">Select {quoteCcy} discount curve</option>
                  {quoteCurves.map((curve) => (
                    <option key={curve.id} value={curve.curve_code}>
                      {curve.curve_code} · v{curve.version}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Grid calendar">
                <Select value={calendar} onChange={(e) => setCalendar(e.target.value)}>
                  {CALENDAR_OPTIONS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Day count">
                <Select value={dayCount} onChange={(e) => setDayCount(e.target.value)}>
                  {FX_DAY_COUNT_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Basis bps"
                hint="Explicit additive spread on the base leg — 0 is textbook CIP."
              >
                <Input
                  value={basisBps}
                  onChange={(e) => setBasisBps(e.target.value)}
                  inputMode="decimal"
                  className="font-mono"
                />
              </Field>
              <Field
                label="Forward tenors"
                hint="Comma-separated, ascending tenors such as 1M, 3M, 6M, 12M."
                className="sm:col-span-2"
              >
                <Input
                  value={tenors}
                  onChange={(e) => setTenors(e.target.value)}
                  className="font-mono"
                />
              </Field>
              <div className="flex items-center justify-between gap-3 border-t border-border-light pt-4 sm:col-span-2">
                <p className="text-caption text-slate">
                  {missingCurve
                    ? `No approved discount curve is available for ${
                        baseCurves.length === 0 ? baseCcy : quoteCcy
                      }. Register and approve one before constructing.`
                    : 'The result records the input digest and operator audit event. It is not automatically delivered to banks.'}
                </p>
                <Button
                  icon={<Calculator size={14} />}
                  loading={busy}
                  disabled={disabled}
                  onClick={() => void construct()}
                >
                  Construct
                </Button>
              </div>
              {error && (
                <div className="sm:col-span-2">
                  <ErrorPanel error={error} context="Constructing FX forwards" />
                </div>
              )}
            </div>
          )}
        </SectionCard>

        <div className="space-y-5">
          {!result ? (
            <SectionCard title="Outright forward result" noPadding>
              <EmptyState
                title="No FX forward constructed"
                description="Choose two approved curves and construct a tenor grid. The result records the input digest and an operator audit event."
              />
            </SectionCard>
          ) : (
            <>
              <FxForwardChart result={result} />
              <SectionCard
                title="Outright forward result"
                subtitle="CIP forward = spot × DF base / DF quote. Forward points are outright minus spot."
                actions={
                  <div className="flex flex-wrap items-center gap-2">
                    <Chip mono>{result.pair}</Chip>
                    <Chip tone="accent">{result.day_count}</Chip>
                    <Chip tone={result.basis_calibrated ? 'ok' : 'warn'}>
                      {result.basis_calibrated ? 'basis calibrated' : 'basis assumption'}
                    </Chip>
                  </div>
                }
                noPadding
                footer={
                  <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span>
                      as of <span className="font-mono text-ink">{fmtDate(result.as_of)}</span>
                    </span>
                    <span>
                      digest <span className="font-mono text-ink">{result.input_digest}</span>
                    </span>
                  </span>
                }
              >
                <DataTable columns={resultColumns} rows={result.rows} density="compact" />
              </SectionCard>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
