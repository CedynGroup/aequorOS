'use client';

import { useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';
import {
  Button,
  Chip,
  DataTable,
  ErrorPanel,
  Field,
  Input,
  SectionCard,
  Select,
  type Column,
} from '@/components/ui';
import { DASH } from '@/lib/format';
import {
  putDeskResearchAdjustments,
  toApiError,
  type ApiError,
  type DeskDetermination,
  type DeskResearchAdjustment,
} from '@/lib/api';

/**
 * Track-1 research adjustments editor (draft only) / read-only ledger
 * (post-submit). Determination-scoped judgment — override / additive bps /
 * assumption note — that enters the package digest without rewriting the
 * methodology register. Save recomputes the package.
 */
export function AdjustmentsPanel({
  determination,
  busy,
  onSaved,
}: {
  determination: DeskDetermination;
  busy: boolean;
  onSaved: () => void;
}) {
  const existing = determination.research_adjustments ?? [];
  const [seriesCode, setSeriesCode] = useState('GHS.LENDING.INDICATOR');
  const [kind, setKind] = useState<DeskResearchAdjustment['kind']>('override');
  const [value, setValue] = useState('');
  const [rationale, setRationale] = useState('');
  const [rows, setRows] = useState<DeskResearchAdjustment[]>(existing);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    setRows(determination.research_adjustments ?? []);
  }, [determination.research_adjustments]);

  function addRow() {
    if (!seriesCode.trim()) return;
    if ((kind === 'override' || kind === 'additive_bps') && (!rationale.trim() || !value.trim())) {
      return;
    }
    const next: DeskResearchAdjustment = {
      series_code: seriesCode.trim(),
      kind,
      value: kind === 'assumption_note' ? null : value.trim(),
      rationale: rationale.trim(),
    };
    setRows((prev) => {
      const without = prev.filter((r) => r.series_code !== next.series_code);
      return [...without, next].sort((a, b) => a.series_code.localeCompare(b.series_code));
    });
    setValue('');
    setRationale('');
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await putDeskResearchAdjustments(
        determination.id,
        rows.map((r) => ({
          series_code: r.series_code,
          kind: r.kind,
          value: r.value,
          rationale: r.rationale,
        })),
      );
      onSaved();
    } catch (err) {
      setError(toApiError(err));
    }
    setSaving(false);
  }

  const baseColumns: Column<DeskResearchAdjustment>[] = [
    { key: 'series', header: 'Series', render: (a) => <span className="font-mono text-caption text-ink">{a.series_code}</span> },
    { key: 'kind', header: 'Kind', render: (a) => <Chip>{a.kind.replace(/_/g, ' ')}</Chip> },
    { key: 'value', header: 'Value', numeric: true, render: (a) => a.value ?? DASH },
    { key: 'rationale', header: 'Rationale', render: (a) => <span className="text-caption text-slate">{a.rationale}</span> },
  ];

  if (determination.status !== 'draft') {
    return (
      <SectionCard
        title="Research adjustments"
        subtitle="Determination-scoped Track-1 judgment applied to this package."
        noPadding
      >
        {existing.length === 0 ? (
          <p className="px-4 py-4 text-caption text-slate">No research adjustments on this package.</p>
        ) : (
          <DataTable columns={baseColumns} rows={existing} density="compact" />
        )}
      </SectionCard>
    );
  }

  const stagedColumns: Column<DeskResearchAdjustment>[] = [
    ...baseColumns,
    {
      key: 'remove',
      header: '',
      align: 'right',
      render: (a) => (
        <Button
          variant="ghost"
          size="sm"
          icon={<Trash2 size={13} />}
          onClick={() => setRows((p) => p.filter((x) => x.series_code !== a.series_code))}
        >
          Remove
        </Button>
      ),
    },
  ];

  return (
    <SectionCard
      title="Research adjustments"
      subtitle="Track-1 weekly judgment only — does not rewrite the methodology register. Override and additive bps require a numeric value and rationale. Saving recomputes the package digest."
    >
      {rows.length > 0 && (
        <div className="mb-4 overflow-hidden rounded border border-border-light">
          <DataTable columns={stagedColumns} rows={rows} density="compact" />
        </div>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Series" className="w-56">
          <Input className="font-mono" value={seriesCode} onChange={(e) => setSeriesCode(e.target.value)} />
        </Field>
        <Field label="Kind">
          <Select value={kind} onChange={(e) => setKind(e.target.value as DeskResearchAdjustment['kind'])}>
            <option value="override">override</option>
            <option value="additive_bps">additive bps</option>
            <option value="assumption_note">assumption note</option>
          </Select>
        </Field>
        {kind !== 'assumption_note' && (
          <Field label={kind === 'additive_bps' ? 'Bps' : 'Value (%)'} className="w-28">
            <Input className="font-mono" value={value} onChange={(e) => setValue(e.target.value)} />
          </Field>
        )}
        <Field label="Rationale" className="min-w-[14rem] flex-1">
          <Input value={rationale} onChange={(e) => setRationale(e.target.value)} />
        </Field>
        <Button onClick={addRow}>Add</Button>
      </div>
      <div className="mt-4">
        <Button onClick={() => void save()} loading={saving} disabled={busy || saving}>
          Save adjustments &amp; recompute
        </Button>
      </div>
      {error && (
        <div className="mt-3">
          <ErrorPanel error={error} context="Saving adjustments" />
        </div>
      )}
    </SectionCard>
  );
}
