'use client';

import { useEffect, useMemo, useState } from 'react';
import { Calculator, Info, Loader2 } from 'lucide-react';
import {
  constructFxForward,
  listCurveDefinitions,
  listDeskDeterminations,
  toApiError,
  type ApiError,
  type FxForwardConstructResponse,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { DASH, fmtDate } from '@/lib/format';
import { Chip, ErrorPanel, PageHeader, SkeletonRows } from '@/components/ui';

const DEFAULT_TENORS = '1M, 3M, 6M, 12M';

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
    () => [...new Set((determinations.data?.determinations ?? []).map((row) => row.cob_date))].sort().reverse(),
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
          day_count: 'ACT_365F',
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

  return (
    <div>
      <PageHeader
        title="FX forwards"
        sub="Covered-interest-parity outright-forward preview from two approved published curves and an FX spot. This is an auditable staff preview; publication remains a separate governed desk action."
      />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(26rem,1.1fr)]">
        <section className="card overflow-hidden">
          <div className="border-b border-border-light px-5 py-4">
            <h2 className="text-h3 text-navy">Construction inputs</h2>
            <p className="mt-1 text-caption text-slate">Use approved, currency-matched discount curves at a published COB. A non-zero basis is an explicit assumption, not a calibrated cross-currency basis.</p>
          </div>
          {definitions.loading && <SkeletonRows rows={5} />}
          {definitions.error && <div className="p-4"><ErrorPanel error={definitions.error} onRetry={definitions.reload} context="Loading published curves" /></div>}
          {definitions.data && (
            <div className="grid gap-4 p-5 sm:grid-cols-2">
              <label className="block space-y-1"><span className="text-caption font-medium text-slate">Base currency</span><input value={baseCcy} onChange={(e) => setBaseCcy(e.target.value.toUpperCase())} maxLength={3} className="w-full rounded border border-border bg-surface-base px-3 py-2 font-mono text-body text-ink" /></label>
              <label className="block space-y-1"><span className="text-caption font-medium text-slate">Quote currency</span><input value={quoteCcy} onChange={(e) => setQuoteCcy(e.target.value.toUpperCase())} maxLength={3} className="w-full rounded border border-border bg-surface-base px-3 py-2 font-mono text-body text-ink" /></label>
              <label className="block space-y-1"><span className="text-caption font-medium text-slate">Spot (quote per base)</span><input value={spot} onChange={(e) => setSpot(e.target.value)} inputMode="decimal" placeholder="e.g. 12.8500" className="w-full rounded border border-border bg-surface-base px-3 py-2 font-mono text-body text-ink" /></label>
              <label className="block space-y-1"><span className="text-caption font-medium text-slate">Published COB</span><select value={asOf} onChange={(e) => setAsOf(e.target.value)} className="w-full rounded border border-border bg-surface-base px-3 py-2 text-body text-ink"><option value="">Select published COB</option>{publishedCobs.map((cob) => <option key={cob} value={cob}>{fmtDate(cob)}</option>)}</select></label>
              <label className="block space-y-1"><span className="text-caption font-medium text-slate">Base discount curve ({baseCcy})</span><select value={baseCurve} onChange={(e) => setBaseCurve(e.target.value)} className="w-full rounded border border-border bg-surface-base px-3 py-2 font-mono text-body text-ink"><option value="">Select {baseCcy} discount curve</option>{baseCurves.map((curve) => <option key={curve.id} value={curve.curve_code}>{curve.curve_code} · v{curve.version}</option>)}</select></label>
              <label className="block space-y-1"><span className="text-caption font-medium text-slate">Quote discount curve ({quoteCcy})</span><select value={quoteCurve} onChange={(e) => setQuoteCurve(e.target.value)} className="w-full rounded border border-border bg-surface-base px-3 py-2 font-mono text-body text-ink"><option value="">Select {quoteCcy} discount curve</option>{quoteCurves.map((curve) => <option key={curve.id} value={curve.curve_code}>{curve.curve_code} · v{curve.version}</option>)}</select></label>
              <label className="block space-y-1"><span className="text-caption font-medium text-slate">Grid calendar</span><select value={calendar} onChange={(e) => setCalendar(e.target.value)} className="w-full rounded border border-border bg-surface-base px-3 py-2 text-body text-ink"><option>GHANA</option><option>USA</option><option>NIGERIA</option><option>KENYA</option><option>SOUTH_AFRICA</option></select></label>
              <label className="block space-y-1"><span className="text-caption font-medium text-slate">Basis bps</span><input value={basisBps} onChange={(e) => setBasisBps(e.target.value)} inputMode="decimal" className="w-full rounded border border-border bg-surface-base px-3 py-2 font-mono text-body text-ink" /></label>
              <label className="block space-y-1 sm:col-span-2"><span className="text-caption font-medium text-slate">Forward tenors</span><input value={tenors} onChange={(e) => setTenors(e.target.value)} className="w-full rounded border border-border bg-surface-base px-3 py-2 font-mono text-body text-ink" /><span className="block text-micro text-slate">Comma-separated, ascending tenors such as 1M, 3M, 6M, 12M.</span></label>
              <div className="sm:col-span-2 flex items-center justify-between gap-3 border-t border-border-light pt-4">
                <p className="text-caption text-slate">{baseCurves.length === 0 || quoteCurves.length === 0 ? `No approved discount curve is available for ${baseCurves.length === 0 ? baseCcy : quoteCcy}. Register and approve one before constructing.` : 'The result records the input digest and operator audit event. It is not automatically delivered to banks.'}</p>
                <button type="button" onClick={() => void construct()} disabled={disabled} className="btn-primary inline-flex shrink-0 items-center gap-1.5 px-3 py-2 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50">{busy ? <Loader2 size={14} className="animate-spin" /> : <Calculator size={14} />} Construct</button>
              </div>
            </div>
          )}
          {error && <div className="px-5 pb-5"><ErrorPanel error={error} context="Constructing FX forwards" /></div>}
        </section>

        <section className="card overflow-hidden">
          <div className="border-b border-border-light px-5 py-4">
            <h2 className="text-h3 text-navy">Outright forward result</h2>
            <p className="mt-1 text-caption text-slate">CIP forward = spot × DF base / DF quote. Forward points are outright minus spot.</p>
          </div>
          {!result && <div className="flex min-h-72 items-center justify-center p-8 text-center"><div><Info size={20} className="mx-auto text-slate-light" /><p className="mt-3 text-body font-medium text-navy">No FX forward constructed</p><p className="mt-1 text-caption text-slate">Choose two approved curves and construct a tenor grid.</p></div></div>}
          {result && (
            <div>
              <div className="flex flex-wrap gap-2 border-b border-border-light px-5 py-3"><Chip mono>{result.pair}</Chip><Chip tone="accent">{result.day_count}</Chip><Chip tone={result.basis_calibrated ? 'ok' : 'warn'}>{result.basis_calibrated ? 'basis calibrated' : 'basis assumption'}</Chip><span className="ml-auto text-caption text-slate">as of {fmtDate(result.as_of)}</span></div>
              <table className="w-full text-body"><thead><tr className="bg-surface text-left text-micro uppercase tracking-wide text-slate"><th className="px-5 py-2.5 font-medium">Date</th><th className="px-3 py-2.5 text-right font-medium">Forward</th><th className="px-3 py-2.5 text-right font-medium">Points</th><th className="px-5 py-2.5 text-right font-medium">DF base / quote</th></tr></thead><tbody>{result.rows.map((row) => <tr key={row.date} className="border-t border-border-light"><td className="px-5 py-2.5 font-mono text-caption text-ink">{fmtDate(row.date)}</td><td className="px-3 py-2.5 text-right font-mono text-ink">{row.forward_rate.toFixed(4)}</td><td className="px-3 py-2.5 text-right font-mono text-slate">{row.forward_points >= 0 ? '+' : ''}{row.forward_points.toFixed(4)}</td><td className="px-5 py-2.5 text-right font-mono text-caption text-slate">{row.df_base.toFixed(6)} / {row.df_quote.toFixed(6)}</td></tr>)}</tbody></table>
              <div className="border-t border-border-light px-5 py-3 text-micro text-slate">Digest <span className="font-mono text-ink">{result.input_digest}</span></div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
