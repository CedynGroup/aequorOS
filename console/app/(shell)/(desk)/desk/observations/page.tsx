'use client';

import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { ChevronLeft, ChevronRight, CopyPlus, PenLine } from 'lucide-react';
import {
  createDeskObservation,
  listDeskObservations,
  type DeskObservation,
  type DeskObservationUnit,
} from '@/lib/api';
import { useApi, useMutation } from '@/lib/use-api';
import { fmtDate, fmtTs, DASH } from '@/lib/format';
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

/**
 * /desk/observations — the desk's raw-material ledger and the MANUAL-ENTRY
 * fallback the spec mandates for every source (§3: BoG publishes HTML/PDF
 * only and blocks automated access).
 *
 * Sources: GET /operator/v1/desk/observations (series-prefix + as-of range +
 * superseded filters AND limit/offset paging are all applied server-side — the
 * ledger holds ~450k rows, so it is never fetched whole) and
 * POST /operator/v1/desk/observations for manual entry. A re-entry for the
 * same (series, as-of) supersedes append-only — the old row keeps existing.
 *
 * Query params (used by Sources → "manual fallback" links): ?series=<prefix>
 * pre-fills the filter and the entry form; &entry=1 opens the form.
 */

const UNITS: DeskObservationUnit[] = ['pct', 'rate', 'ghs', 'index'];

/** One server page. Backend default is 100, hard cap 500. */
const PAGE_SIZE = 100;
/** Series-prefix input debounce so typing doesn't refire the query per keystroke. */
const PREFIX_DEBOUNCE_MS = 300;

function coerceUnit(unit: string): DeskObservationUnit {
  return (UNITS as string[]).includes(unit) ? (unit as DeskObservationUnit) : 'pct';
}

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

  const formRef = useRef<HTMLDivElement>(null);

  // ---- filters — every one drives the SERVER query (see docstring). The
  // series prefix is debounced; the rest apply on change. Any filter change
  // resets paging to offset 0. --------------------------------------------
  const [prefix, setPrefix] = useState(initialSeries);
  const [debouncedPrefix, setDebouncedPrefix] = useState(initialSeries);
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [includeSuperseded, setIncludeSuperseded] = useState(false);
  const [offset, setOffset] = useState(0);

  // Debounce the prefix input; committing a new prefix jumps back to page 1.
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedPrefix(prefix.trim());
      setOffset(0);
    }, PREFIX_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [prefix]);

  const { data, error, loading, reload } = useApi(
    () =>
      listDeskObservations({
        seriesCode: debouncedPrefix || undefined,
        asOfFrom: fromDate || undefined,
        asOfTo: toDate || undefined,
        includeSuperseded,
        limit: PAGE_SIZE,
        offset,
      }),
    [debouncedPrefix, fromDate, toDate, includeSuperseded, offset],
  );

  // Pager math off the server-echoed window (fall back to local state pre-load).
  const total = data?.total ?? 0;
  const pageOffset = data?.offset ?? offset;
  const rows = data?.observations ?? [];
  const rangeFrom = total === 0 ? 0 : pageOffset + 1;
  const rangeTo = pageOffset + rows.length;
  const hasPrev = offset > 0;
  const hasNext = rangeTo < total;

  // ---- manual entry form --------------------------------------------------
  const [entryOpen, setEntryOpen] = useState(openEntry);
  const [seriesCode, setSeriesCode] = useState(initialSeries);
  const [asOfDate, setAsOfDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [value, setValue] = useState('');
  const [unit, setUnit] = useState<DeskObservationUnit>('pct');
  const [note, setNote] = useState('');
  const [supersedes, setSupersedes] = useState<DeskObservation | null>(null);

  const valueValid = value.trim() !== '' && !Number.isNaN(Number(value.trim()));
  const entryComplete = seriesCode.trim() !== '' && asOfDate !== '' && valueValid;

  const createObs = useMutation(createDeskObservation, {
    successMessage: (row) => `Recorded ${row.series_code} = ${row.value} (${row.unit})`,
    errorContext: 'Record observation',
    onSuccess: () => {
      setValue('');
      setNote('');
      setSupersedes(null);
      // Jump to page 1 (newest as-of first) so the new row is visible; if already
      // there, setOffset is a no-op so reload() forces the refetch.
      if (offset === 0) reload();
      else setOffset(0);
    },
  });

  function submitEntry() {
    if (!entryComplete) return;
    void createObs.mutate({
      series_code: seriesCode.trim(),
      as_of_date: asOfDate,
      value: value.trim(),
      unit,
      attributes: note.trim() ? { note: note.trim() } : {},
    });
  }

  /** Supersede affordance: prefill the entry form from an existing row so the
   *  operator only re-keys the corrected value (append-only correction). */
  function supersedeFrom(row: DeskObservation) {
    setSeriesCode(row.series_code);
    setUnit(coerceUnit(row.unit));
    setAsOfDate(row.as_of_date.slice(0, 10));
    setValue('');
    setNote('');
    setSupersedes(row);
    setEntryOpen(true);
    createObs.reset();
    window.requestAnimationFrame(() =>
      formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
    );
  }

  const columns = useMemo<Column<DeskObservation>[]>(() => {
    const cols: Column<DeskObservation>[] = [
      {
        key: 'series',
        header: 'Series',
        sortable: true,
        sortAccessor: (o) => o.series_code,
        render: (o) => <span className="font-mono text-caption text-ink">{o.series_code}</span>,
      },
      {
        key: 'as_of',
        header: 'As of',
        sortable: true,
        sortAccessor: (o) => o.as_of_date,
        render: (o) => <span className="text-caption text-ink">{fmtDate(o.as_of_date)}</span>,
      },
      {
        key: 'value',
        header: 'Value',
        numeric: true,
        sortable: true,
        sortAccessor: (o) => Number(o.value),
        render: (o) => o.value,
      },
      { key: 'unit', header: 'Unit', render: (o) => <span className="text-caption text-slate">{o.unit}</span> },
      {
        key: 'quality',
        header: 'Quality flags',
        render: (o) =>
          o.quality_flags.length === 0 ? (
            <span className="text-slate-light">{DASH}</span>
          ) : (
            <span className="flex flex-wrap gap-1">
              {o.quality_flags.map((f, i) => (
                <Chip key={i} tone="warn">
                  {String(f).replace(/_/g, ' ')}
                </Chip>
              ))}
            </span>
          ),
      },
      { key: 'provenance', header: 'Provenance', render: (o) => <ProvenanceCell row={o} /> },
      {
        key: 'recorded',
        header: 'Recorded',
        sortable: true,
        sortAccessor: (o) => o.created_at,
        render: (o) => (
          <span className="text-caption text-slate" title={fmtTs(o.created_at)}>
            {fmtDate(o.created_at)}
          </span>
        ),
      },
    ];
    if (includeSuperseded) {
      cols.push({
        key: 'generation',
        header: 'Generation',
        render: (o) =>
          o.superseded_by ? (
            <Chip tone="warn" title={`superseded by ${o.superseded_by}`}>
              superseded
            </Chip>
          ) : (
            <Chip tone="ok">current</Chip>
          ),
      });
    }
    cols.push({
      key: 'actions',
      header: '',
      align: 'right',
      render: (o) => (
        <Button
          size="sm"
          variant="ghost"
          icon={<CopyPlus size={13} />}
          onClick={() => supersedeFrom(o)}
          title="Prefill the entry form to record a correction"
        >
          Supersede
        </Button>
      ),
    });
    return cols;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeSuperseded]);

  return (
    <div>
      <PageHeader
        title="Observations"
        sub="Every raw data point the desk holds — parser-written from captures or manually entered. Corrections supersede append-only."
        action={
          <Button
            icon={<PenLine size={15} />}
            variant={entryOpen ? 'secondary' : 'primary'}
            onClick={() => setEntryOpen((v) => !v)}
          >
            Manual entry
          </Button>
        }
      />

      {/* ------------------------------------------------ manual entry form */}
      {entryOpen && (
        <div ref={formRef}>
          <SectionCard
            title={supersedes ? 'Supersede observation' : 'Manual entry'}
            subtitle={
              supersedes
                ? `Recording a correction for ${supersedes.series_code} as of ${fmtDate(supersedes.as_of_date)} — the prior row is kept, never edited.`
                : 'The human-assisted fallback for every source: a first-class observation with your operator identity as provenance.'
            }
            actions={
              supersedes ? (
                <Button size="sm" variant="ghost" onClick={() => setSupersedes(null)}>
                  Clear
                </Button>
              ) : undefined
            }
            className="mb-5"
          >
            <form
              onSubmit={(e) => {
                e.preventDefault();
                submitEntry();
              }}
            >
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <Field label="Series code" required className="lg:col-span-2">
                  <Input
                    className="font-mono"
                    value={seriesCode}
                    onChange={(e) => setSeriesCode(e.target.value.toUpperCase())}
                    placeholder="e.g. GHS.TBILL.91.DISCOUNT"
                    required
                  />
                </Field>
                <Field label="As-of date" required>
                  <Input
                    type="date"
                    value={asOfDate}
                    onChange={(e) => setAsOfDate(e.target.value)}
                    required
                  />
                </Field>
                <Field label="Value" required>
                  <Input
                    className="font-mono"
                    invalid={value !== '' && !valueValid}
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    placeholder="e.g. 24.5000"
                    inputMode="decimal"
                    required
                  />
                </Field>
                <Field label="Unit">
                  <Select value={unit} onChange={(e) => setUnit(e.target.value as DeskObservationUnit)}>
                    {UNITS.map((u) => (
                      <option key={u} value={u}>
                        {u}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field
                  label="Note (optional — stored on the observation)"
                  className="sm:col-span-2 lg:col-span-5"
                >
                  <Input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="e.g. read from the BoG auction notice PDF of 2026-08-07"
                  />
                </Field>
              </div>
              <div className="mt-3 flex items-center gap-3">
                <Button type="submit" loading={createObs.loading} disabled={!entryComplete}>
                  {supersedes ? 'Record correction' : 'Record observation'}
                </Button>
                <span className="text-caption text-slate">
                  Re-entering an existing (series, as-of) pair supersedes the current row.
                </span>
              </div>
            </form>
          </SectionCard>
        </div>
      )}

      {/* ------------------------------------------------------- filters */}
      <SectionCard title="Filters" className="mb-4">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Series code prefix">
            <Input
              className="w-64 font-mono"
              value={prefix}
              onChange={(e) => setPrefix(e.target.value.toUpperCase())}
              placeholder="GHS.TBILL."
            />
          </Field>
          <Field label="From">
            <Input
              type="date"
              value={fromDate}
              onChange={(e) => {
                setFromDate(e.target.value);
                setOffset(0);
              }}
            />
          </Field>
          <Field label="To">
            <Input
              type="date"
              value={toDate}
              onChange={(e) => {
                setToDate(e.target.value);
                setOffset(0);
              }}
            />
          </Field>
          <label className="mb-2 flex items-center gap-2 text-caption text-slate">
            <input
              type="checkbox"
              checked={includeSuperseded}
              onChange={(e) => {
                setIncludeSuperseded(e.target.checked);
                setOffset(0);
              }}
            />
            Include superseded rows
          </label>
          {data && (
            <span className="mb-2 ml-auto text-caption text-slate tnum">
              {total.toLocaleString()} matching {total === 1 ? 'observation' : 'observations'}
            </span>
          )}
        </div>
      </SectionCard>

      {/* --------------------------------------------------------- table */}
      <SectionCard title="Observation ledger" noPadding>
        {error && (
          <div className="p-4">
            <ErrorPanel error={error} onRetry={reload} context="Loading observations" />
          </div>
        )}
        {!error && loading && !data && <SkeletonRows rows={8} />}
        {!error && data && rows.length === 0 && (
          <EmptyState
            title="No observations match"
            hint="Adjust the filters, or record the first data point with Manual entry — the desk holds nothing it did not capture or enter."
          />
        )}
        {!error && data && rows.length > 0 && (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              density="compact"
              rowClassName={(o) => (o.superseded_by ? 'opacity-60' : '')}
            />
            <div className="flex items-center justify-between gap-3 border-t border-border-light px-4 py-2.5 text-caption text-slate">
              <span className="tnum">
                Showing {rangeFrom.toLocaleString()}–{rangeTo.toLocaleString()} of{' '}
                {total.toLocaleString()}
                {loading && <span className="ml-2 text-slate-light">loading…</span>}
              </span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                  disabled={!hasPrev || loading}
                  className="inline-flex items-center gap-1 rounded border border-border-light px-2 py-1 hover:bg-surface disabled:opacity-40"
                >
                  <ChevronLeft size={13} aria-hidden /> Prev
                </button>
                <button
                  type="button"
                  onClick={() => setOffset((o) => o + PAGE_SIZE)}
                  disabled={!hasNext || loading}
                  className="inline-flex items-center gap-1 rounded border border-border-light px-2 py-1 hover:bg-surface disabled:opacity-40"
                >
                  Next <ChevronRight size={13} aria-hidden />
                </button>
              </div>
            </div>
          </>
        )}
      </SectionCard>
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
