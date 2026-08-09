'use client';

import { Suspense, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { CheckCircle2, Loader2, PenLine } from 'lucide-react';
import {
  createDeskObservation,
  listDeskObservations,
  toApiError,
  type ApiError,
  type DeskObservation,
  type DeskObservationUnit,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, fmtTs, DASH } from '@/lib/format';
import {
  Chip,
  EmptyState,
  ErrorPanel,
  PageHeader,
  SkeletonRows,
} from '@/components/ui';

/**
 * /desk/observations — the desk's raw-material ledger and the MANUAL-ENTRY
 * fallback the spec mandates for every source (§3: BoG publishes HTML/PDF
 * only and blocks automated access).
 *
 * Sources: GET /operator/v1/desk/observations (exact-match backend filters;
 * the prefix + date-range narrowing here is client-side) and
 * POST /operator/v1/desk/observations for manual entry. A re-entry for the
 * same (series, as-of) supersedes append-only — the old row keeps existing.
 *
 * Query params (used by Sources → "manual fallback" links): ?series=<prefix>
 * pre-fills the filter and the entry form; &entry=1 opens the form.
 */

const UNITS: DeskObservationUnit[] = ['pct', 'rate', 'ghs', 'index'];

const inputClass =
  'w-full rounded-md border border-border bg-surface-base px-3 py-2 text-body text-ink placeholder:text-slate-light focus:border-focus focus:outline-none';
const labelClass = 'mb-1 block text-caption font-medium text-slate';

function ProvenanceCell({ row }: { row: DeskObservation }) {
  if (row.entered_by) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <Chip tone="accent">manual</Chip>
        <span className="font-mono text-micro text-slate">{row.entered_by}</span>
      </span>
    );
  }
  if (row.capture_id) {
    return (
      <Chip mono title={`capture ${row.capture_id}`}>
        capture
      </Chip>
    );
  }
  return <span className="text-slate-light">{DASH}</span>;
}

function ObservationsInner() {
  const search = useSearchParams();
  const initialSeries = search.get('series') ?? '';
  const openEntry = search.get('entry') === '1';

  // ---- filters (prefix + date range are client-side; superseded is a real
  // backend flag) ----------------------------------------------------------
  const [prefix, setPrefix] = useState(initialSeries);
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [includeSuperseded, setIncludeSuperseded] = useState(false);

  const { data, error, loading, reload } = useApi(
    () => listDeskObservations({ includeSuperseded }),
    [includeSuperseded],
  );

  const filtered = useMemo(() => {
    if (!data) return null;
    const p = prefix.trim().toUpperCase();
    return data.observations.filter((o) => {
      if (p && !o.series_code.toUpperCase().startsWith(p)) return false;
      if (fromDate && o.as_of_date < fromDate) return false;
      if (toDate && o.as_of_date > toDate) return false;
      return true;
    });
  }, [data, prefix, fromDate, toDate]);

  // ---- manual entry form --------------------------------------------------
  const [entryOpen, setEntryOpen] = useState(openEntry);
  const [seriesCode, setSeriesCode] = useState(initialSeries);
  const [asOfDate, setAsOfDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [value, setValue] = useState('');
  const [unit, setUnit] = useState<DeskObservationUnit>('pct');
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [entryError, setEntryError] = useState<ApiError | null>(null);
  const [created, setCreated] = useState<DeskObservation | null>(null);

  const valueValid = value.trim() !== '' && !Number.isNaN(Number(value.trim()));
  const entryComplete = seriesCode.trim() !== '' && asOfDate !== '' && valueValid;

  async function submitEntry() {
    setSubmitting(true);
    setEntryError(null);
    setCreated(null);
    try {
      const row = await createDeskObservation({
        series_code: seriesCode.trim(),
        as_of_date: asOfDate,
        value: value.trim(),
        unit,
        attributes: note.trim() ? { note: note.trim() } : {},
      });
      setCreated(row);
      setValue('');
      setNote('');
      reload(); // the created observation appears in the table below
    } catch (err) {
      setEntryError(toApiError(err));
    }
    setSubmitting(false);
  }

  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <PageHeader
          title="Observations"
          sub="Every raw data point the desk holds — parser-written from captures or manually entered. Corrections supersede append-only."
        />
        <button
          type="button"
          onClick={() => setEntryOpen((v) => !v)}
          className="btn-primary inline-flex shrink-0 items-center gap-1.5 px-3 py-2 text-body font-medium"
        >
          <PenLine size={15} /> Manual entry
        </button>
      </div>

      {/* ------------------------------------------------ manual entry form */}
      {entryOpen && (
        <form
          className="card mb-5 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (entryComplete && !submitting) void submitEntry();
          }}
        >
          <p className="mb-3 text-caption text-slate">
            The human-assisted fallback for every source: a first-class observation with your
            operator identity as provenance. Entering a value for an existing (series, as-of)
            pair supersedes the current row — the old one is kept, never edited.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <label className="block lg:col-span-2">
              <span className={labelClass}>Series code</span>
              <input
                className={`${inputClass} font-mono`}
                value={seriesCode}
                onChange={(e) => setSeriesCode(e.target.value.toUpperCase())}
                placeholder="e.g. GHS.TBILL.91.DISCOUNT"
                required
              />
            </label>
            <label className="block">
              <span className={labelClass}>As-of date</span>
              <input
                type="date"
                className={inputClass}
                value={asOfDate}
                onChange={(e) => setAsOfDate(e.target.value)}
                required
              />
            </label>
            <label className="block">
              <span className={labelClass}>Value</span>
              <input
                className={`${inputClass} font-mono ${value && !valueValid ? 'border-critical' : ''}`}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="e.g. 24.5000"
                inputMode="decimal"
                required
              />
            </label>
            <label className="block">
              <span className={labelClass}>Unit</span>
              <select
                className={inputClass}
                value={unit}
                onChange={(e) => setUnit(e.target.value as DeskObservationUnit)}
              >
                {UNITS.map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </select>
            </label>
            <label className="block sm:col-span-2 lg:col-span-5">
              <span className={labelClass}>Note (optional — stored on the observation)</span>
              <input
                className={inputClass}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. read from the BoG auction notice PDF of 2026-08-07"
              />
            </label>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <button
              type="submit"
              disabled={!entryComplete || submitting}
              className="btn-primary inline-flex items-center gap-1.5 px-4 py-2 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting && <Loader2 size={14} className="animate-spin" />}
              Record observation
            </button>
            {created && (
              <span className="inline-flex items-center gap-1.5 text-caption text-success">
                <CheckCircle2 size={14} />
                Recorded {created.series_code} = {created.value} ({created.unit}) as of{' '}
                {fmtDate(created.as_of_date)}
              </span>
            )}
          </div>
          {entryError && (
            <div className="mt-3">
              <ErrorPanel error={entryError} context="Recording the observation" />
            </div>
          )}
        </form>
      )}

      {/* ------------------------------------------------------- filters */}
      <div className="card mb-4 flex flex-wrap items-end gap-3 p-4">
        <label className="block">
          <span className={labelClass}>Series code prefix</span>
          <input
            className={`${inputClass} w-64 font-mono`}
            value={prefix}
            onChange={(e) => setPrefix(e.target.value.toUpperCase())}
            placeholder="GHS.TBILL."
          />
        </label>
        <label className="block">
          <span className={labelClass}>From</span>
          <input
            type="date"
            className={inputClass}
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
          />
        </label>
        <label className="block">
          <span className={labelClass}>To</span>
          <input
            type="date"
            className={inputClass}
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
          />
        </label>
        <label className="mb-2 flex items-center gap-2 text-caption text-slate">
          <input
            type="checkbox"
            checked={includeSuperseded}
            onChange={(e) => setIncludeSuperseded(e.target.checked)}
          />
          Include superseded rows
        </label>
        {filtered && (
          <span className="mb-2 ml-auto text-caption text-slate">
            {filtered.length} of {data?.total ?? 0} observations
          </span>
        )}
      </div>

      {/* --------------------------------------------------------- table */}
      <div className="card overflow-x-auto">
        {loading && <SkeletonRows rows={8} />}

        {error && (
          <div className="p-4">
            <ErrorPanel error={error} onRetry={reload} context="Loading observations" />
          </div>
        )}

        {filtered && filtered.length === 0 && (
          <EmptyState
            title="No observations match"
            hint="Adjust the filters, or record the first data point with Manual entry — the desk holds nothing it did not capture or enter."
          />
        )}

        {filtered && filtered.length > 0 && (
          <table className="w-full text-body">
            <thead>
              <tr className="border-b border-border-light bg-surface text-left text-micro uppercase tracking-wide text-slate">
                <th className="px-4 py-2.5 font-medium">Series</th>
                <th className="px-4 py-2.5 font-medium">As of</th>
                <th className="px-4 py-2.5 font-medium text-right">Value</th>
                <th className="px-4 py-2.5 font-medium">Unit</th>
                <th className="px-4 py-2.5 font-medium">Quality flags</th>
                <th className="px-4 py-2.5 font-medium">Provenance</th>
                <th className="px-4 py-2.5 font-medium">Recorded</th>
                {includeSuperseded && <th className="px-4 py-2.5 font-medium">Generation</th>}
              </tr>
            </thead>
            <tbody>
              {filtered.map((o) => (
                <tr
                  key={o.id}
                  className={`border-b border-border-light last:border-b-0 ${
                    o.superseded_by ? 'opacity-60' : ''
                  }`}
                >
                  <td className="px-4 py-2 font-mono text-caption text-ink">{o.series_code}</td>
                  <td className="px-4 py-2 text-caption text-ink">{fmtDate(o.as_of_date)}</td>
                  <td className="num px-4 py-2 text-ink">{o.value}</td>
                  <td className="px-4 py-2 text-caption text-slate">{o.unit}</td>
                  <td className="px-4 py-2">
                    {o.quality_flags.length === 0 ? (
                      <span className="text-slate-light">{DASH}</span>
                    ) : (
                      <span className="flex flex-wrap gap-1">
                        {o.quality_flags.map((f, i) => (
                          <Chip key={i} tone="warn">
                            {String(f).replace(/_/g, ' ')}
                          </Chip>
                        ))}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <ProvenanceCell row={o} />
                  </td>
                  <td className="px-4 py-2 text-caption text-slate" title={fmtTs(o.created_at)}>
                    {fmtDate(o.created_at)}
                  </td>
                  {includeSuperseded && (
                    <td className="px-4 py-2">
                      {o.superseded_by ? (
                        <Chip tone="warn" title={`superseded by ${o.superseded_by}`}>
                          superseded
                        </Chip>
                      ) : (
                        <Chip tone="ok">current</Chip>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default function ObservationsPage() {
  // useSearchParams (the Sources → manual-fallback prefill) requires a
  // Suspense boundary for Next's static prerender of this route.
  return (
    <Suspense fallback={<SkeletonRows rows={8} />}>
      <ObservationsInner />
    </Suspense>
  );
}
