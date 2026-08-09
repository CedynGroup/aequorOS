'use client';

/**
 * Scenario Workbench — the shared treasury stress surface for all five
 * modules. Library (system + desk scenarios), editor, select-and-run
 * analysis, side-by-side comparison, and optional saved analyses.
 *
 * Analysis is compute-only: nothing here writes to the official register.
 * Formal runs and regulatory packs live under Governance.
 */

import { useMemo, useState } from 'react';
import { Lock } from 'lucide-react';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useArchiveStressScenario,
  useCreateStressScenario,
  useDeleteScenarioAnalysis,
  useRunScenarioAnalysis,
  useSaveScenarioAnalysis,
  useSavedAnalyses,
  useScenarioCatalogue,
  useUpdateStressScenario,
} from '@/lib/api/hooks';
import { ApiError } from '@/lib/api/client';
import { fmtCurrency, fmtPct } from '@/lib/format';
import type {
  AnalysisRunRead,
  ScenarioCatalogueEntryRead,
  ScenarioRefIn,
  ScenarioResultRead,
  WorkbenchModule,
} from '@aequoros/risk-service-api';

export type MetricSpec = {
  /** False for metrics where an increase is adverse (outflows, gaps). */
  higherIsBetter?: boolean;
  key: string;
  label: string;
  kind: 'pct' | 'ghs' | 'number';
};

type Props = {
  module: WorkbenchModule;
  metrics: MetricSpec[];
  /** Metric whose status drives the scenario's overall pill + delta column. */
  primaryMetric: string;
};

const STATUS_TONE: Record<string, StatusTone> = {
  green: 'success',
  amber: 'amber',
  red: 'critical',
  na: 'slate',
};

function fmtMetric(spec: MetricSpec, raw: string | undefined): string {
  if (raw === undefined || raw === null) return '—';
  const value = Number(raw);
  if (!Number.isFinite(value)) return raw;
  if (spec.kind === 'pct') return fmtPct(value, 2);
  if (spec.kind === 'ghs') return fmtCurrency(value);
  return value.toLocaleString();
}

function refFor(
  entry: ScenarioCatalogueEntryRead,
  overrides?: Record<string, string>
): ScenarioRefIn {
  const cleaned = Object.fromEntries(
    Object.entries(overrides ?? {}).filter(([, v]) => v.trim() !== '')
  );
  const base: ScenarioRefIn =
    entry.kind === 'system'
      ? { kind: 'system', code: entry.code }
      : { kind: 'custom', scenarioId: entry.scenarioId ?? undefined };
  return Object.keys(cleaned).length > 0 ? { ...base, overrides: cleaned } : base;
}

function entryKey(entry: ScenarioCatalogueEntryRead): string {
  return `${entry.kind}:${entry.scenarioId ?? entry.code}`;
}

type EditorState = {
  scenarioId: string | null; // null = creating
  code: string;
  name: string;
  description: string;
  shocks: { key: string; value: string }[];
};

const EMPTY_EDITOR: EditorState = {
  scenarioId: null,
  code: '',
  name: '',
  description: '',
  shocks: [{ key: '', value: '' }],
};

export default function ScenarioWorkbench({ module, metrics, primaryMetric }: Props) {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const catalogue = useScenarioCatalogue(bankId, module, periodId);
  const runAnalysis = useRunScenarioAnalysis(bankId, module);
  const createScenario = useCreateStressScenario(bankId, module);
  const updateScenario = useUpdateStressScenario(bankId, module);
  const archiveScenario = useArchiveStressScenario(bankId, module);
  const savedAnalyses = useSavedAnalyses(bankId, module);
  const saveAnalysis = useSaveScenarioAnalysis(bankId, module);
  const deleteAnalysis = useDeleteScenarioAnalysis(bankId, module);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisRunRead | null>(null);
  const [saveName, setSaveName] = useState('');
  // Per-run shock tweaks keyed by entryKey → shock key → raw input. Transient:
  // sent as ScenarioRefIn.overrides, never persisted to the scenario.
  const [overrides, setOverrides] = useState<Record<string, Record<string, string>>>({});

  const entries = useMemo(
    () => catalogue.data?.scenarios ?? [],
    [catalogue.data]
  );
  const selectedEntries = entries.filter((entry) => selected.has(entryKey(entry)));
  const primarySpec = metrics.find((m) => m.key === primaryMetric);

  const toggle = (entry: ScenarioCatalogueEntryRead) => {
    const key = entryKey(entry);
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const openEditor = (entry?: ScenarioCatalogueEntryRead, duplicate = false) => {
    setEditorError(null);
    if (!entry) {
      setEditor({ ...EMPTY_EDITOR, shocks: [{ key: '', value: '' }] });
      return;
    }
    setEditor({
      scenarioId: duplicate || entry.kind === 'system' ? null : entry.scenarioId ?? null,
      code:
        duplicate || entry.kind === 'system'
          ? `${entry.code}_desk`.replace(/[^a-z0-9_]/g, '_')
          : entry.code,
      name: duplicate || entry.kind === 'system' ? `${entry.name} (desk)` : entry.name,
      description: entry.description ?? '',
      shocks: Object.entries(entry.shocks).map(([key, value]) => ({ key, value })),
    });
  };

  const submitEditor = async () => {
    if (!editor) return;
    setEditorError(null);
    const shocks: Record<string, string> = {};
    for (const row of editor.shocks) {
      if (row.key.trim()) shocks[row.key.trim()] = row.value.trim();
    }
    try {
      if (editor.scenarioId) {
        await updateScenario.mutateAsync({
          scenarioId: editor.scenarioId,
          payload: { name: editor.name, description: editor.description || undefined, shocks },
        });
      } else {
        await createScenario.mutateAsync({
          code: editor.code,
          name: editor.name,
          description: editor.description || undefined,
          shocks,
        });
      }
      setEditor(null);
    } catch (error) {
      if (error instanceof ApiError) {
        const details = error.details as Record<string, unknown> | null;
        const allowed = details?.['allowed_keys'];
        setEditorError(
          `${error.errorCode ?? error.code ?? 'error'}: ${error.message}` +
            (Array.isArray(allowed) ? ` — allowed keys: ${allowed.join(', ')}` : '')
        );
      } else {
        setEditorError('Could not save the scenario.');
      }
    }
  };

  const execute = async () => {
    if (!periodId || selectedEntries.length === 0) return;
    const outcome = await runAnalysis.mutateAsync({
      reportingPeriodId: periodId,
      scenarios: selectedEntries.map((entry) => refFor(entry, overrides[entryKey(entry)])),
    });
    setAnalysis(outcome);
  };

  const persist = async () => {
    if (!periodId || selectedEntries.length === 0 || !saveName.trim()) return;
    await saveAnalysis.mutateAsync({
      reportingPeriodId: periodId,
      name: saveName.trim(),
      scenarios: selectedEntries.map((entry) => refFor(entry, overrides[entryKey(entry)])),
    });
    setSaveName('');
  };

  const baselineResult = analysis?.results[0];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-6 items-start">
        <div className="xl:col-span-2 space-y-6">
      {/* Scenario library */}
      <SectionCard
        title="Scenario library"
        subtitle="Regulatory scenarios run on the governed parameter levels; desk scenarios are yours to shape"
        noPadding
        actions={
          <button
            type="button"
            className="rounded-md border border-border-light px-3 py-1.5 text-caption font-medium text-navy hover:bg-surface"
            onClick={() => openEditor()}
          >
            New scenario
          </button>
        }
        footer={
          <div className="flex items-center justify-between gap-4">
            <span>
              {selectedEntries.length} selected · analysis computes against current
              positions and changes nothing
            </span>
            <button
              type="button"
              className="rounded-md bg-action px-4 py-2 text-body font-medium text-white hover:bg-action/90 disabled:opacity-50"
              disabled={!periodId || selectedEntries.length === 0 || runAnalysis.isPending}
              onClick={() => void execute()}
            >
              {runAnalysis.isPending ? 'Calculating…' : 'Run analysis'}
            </button>
          </div>
        }
      >
        <QueryBoundary
          isLoading={catalogue.isLoading}
          error={catalogue.error}
          onRetry={() => catalogue.refetch()}
        >
          <ul className="divide-y divide-border-light">
            {entries.map((entry) => {
              const key = entryKey(entry);
              const shockCount = Object.keys(entry.shocks).length;
              return (
                <li key={key} className="px-5 py-3 flex items-center gap-4">
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-action"
                    checked={selected.has(key)}
                    onChange={() => toggle(entry)}
                    aria-label={`Select ${entry.name}`}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className="text-body font-medium text-navy">{entry.name}</p>
                      <StatusPill tone={entry.kind === 'system' ? 'action' : 'slate'}>
                        {entry.kind === 'system' ? 'Regulatory' : 'Desk'}
                      </StatusPill>
                      {!entry.configured && (
                        <StatusPill tone="amber">No parameters configured</StatusPill>
                      )}
                    </div>
                    {entry.description ? (
                      <p className="text-caption text-slate">{entry.description}</p>
                    ) : null}
                    <p className="text-caption text-slate/80">
                      {shockCount === 0
                        ? 'No shocks — current positions as they stand'
                        : `${shockCount} shock parameter${shockCount === 1 ? '' : 's'}`}
                    </p>
                    {selected.has(key) && shockCount > 0 && (
                      <div className="mt-2 rounded-md border border-border-light bg-surface-alt px-3 py-2 space-y-1.5">
                        <p className="text-micro font-medium uppercase tracking-wider text-slate">
                          Adjust for this run
                        </p>
                        {Object.entries(entry.shocks).map(([shockKey, shockValue]) => (
                          <label
                            key={shockKey}
                            className="flex items-center justify-between gap-3"
                          >
                            <span className="font-mono text-caption text-slate truncate">
                              {shockKey}
                            </span>
                            <input
                              type="text"
                              inputMode="decimal"
                              placeholder={String(shockValue)}
                              value={overrides[key]?.[shockKey] ?? ''}
                              onChange={(e) =>
                                setOverrides((current) => ({
                                  ...current,
                                  [key]: { ...current[key], [shockKey]: e.target.value },
                                }))
                              }
                              className="w-24 rounded border border-border-light bg-surface px-2 py-1 text-right font-mono text-caption text-navy tnum placeholder:text-slate-light"
                              aria-label={`Override ${shockKey} for this run`}
                            />
                          </label>
                        ))}
                        <p className="text-micro text-slate-light">
                          Transient — applies to this analysis only, the scenario is unchanged.
                        </p>
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {entry.kind === 'system' && (
                      <span
                        className="text-slate-light"
                        title="Regulatory scenario — parameters are governed; duplicate to adjust"
                        aria-label="Locked regulatory scenario"
                      >
                        <Lock size={12} aria-hidden />
                      </span>
                    )}
                    <button
                      type="button"
                      className="text-caption text-action hover:underline"
                      onClick={() => openEditor(entry, true)}
                    >
                      Duplicate
                    </button>
                    {entry.kind === 'custom' && (
                      <>
                        <button
                          type="button"
                          className="text-caption text-action hover:underline"
                          onClick={() => openEditor(entry)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="text-caption text-slate hover:underline"
                          onClick={() =>
                            entry.scenarioId &&
                            void archiveScenario.mutateAsync({
                              scenarioId: entry.scenarioId,
                              isArchived: true,
                            })
                          }
                        >
                          Archive
                        </button>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </QueryBoundary>
      </SectionCard>

      {/* Editor */}
      {editor && (
        <SectionCard
          title={editor.scenarioId ? 'Edit scenario' : 'New desk scenario'}
          subtitle={`Shock parameters for the ${module} engine — unknown keys are rejected with the allowed list`}
        >
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {!editor.scenarioId && (
                <label className="block">
                  <span className="text-caption text-slate">Code (slug)</span>
                  <input
                    className="mt-1 w-full rounded-md border border-border-light bg-transparent px-3 py-2 text-body text-navy"
                    value={editor.code}
                    onChange={(e) => setEditor({ ...editor, code: e.target.value })}
                    placeholder="desk_wholesale_squeeze"
                  />
                </label>
              )}
              <label className="block">
                <span className="text-caption text-slate">Name</span>
                <input
                  className="mt-1 w-full rounded-md border border-border-light bg-transparent px-3 py-2 text-body text-navy"
                  value={editor.name}
                  onChange={(e) => setEditor({ ...editor, name: e.target.value })}
                />
              </label>
            </div>
            <label className="block">
              <span className="text-caption text-slate">Description</span>
              <input
                className="mt-1 w-full rounded-md border border-border-light bg-transparent px-3 py-2 text-body text-navy"
                value={editor.description}
                onChange={(e) => setEditor({ ...editor, description: e.target.value })}
              />
            </label>
            <div className="space-y-2">
              <span className="text-caption text-slate">
                Shocks · allowed keys: {catalogue.data?.allowedKeys.join(', ')}
                {catalogue.data && catalogue.data.allowedPrefixes.length > 0
                  ? ` · prefixes: ${catalogue.data.allowedPrefixes.join(' ')}`
                  : ''}
              </span>
              {editor.shocks.map((row, index) => (
                // eslint-disable-next-line react/no-array-index-key
                <div key={index} className="flex gap-2">
                  <input
                    className="flex-1 rounded-md border border-border-light bg-transparent px-3 py-2 text-body text-navy font-mono"
                    value={row.key}
                    placeholder="shock key"
                    onChange={(e) => {
                      const shocks = [...editor.shocks];
                      shocks[index] = { ...shocks[index], key: e.target.value };
                      setEditor({ ...editor, shocks });
                    }}
                  />
                  <input
                    className="w-40 rounded-md border border-border-light bg-transparent px-3 py-2 text-body text-navy font-mono"
                    value={row.value}
                    placeholder="value"
                    onChange={(e) => {
                      const shocks = [...editor.shocks];
                      shocks[index] = { ...shocks[index], value: e.target.value };
                      setEditor({ ...editor, shocks });
                    }}
                  />
                  <button
                    type="button"
                    className="text-caption text-slate hover:underline"
                    onClick={() =>
                      setEditor({
                        ...editor,
                        shocks: editor.shocks.filter((_, i) => i !== index),
                      })
                    }
                  >
                    Remove
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="text-caption text-action hover:underline"
                onClick={() =>
                  setEditor({ ...editor, shocks: [...editor.shocks, { key: '', value: '' }] })
                }
              >
                Add shock
              </button>
            </div>
            {editorError && <p className="text-caption text-critical">{editorError}</p>}
            <div className="flex gap-3">
              <button
                type="button"
                className="rounded-md bg-action px-4 py-2 text-body font-medium text-white hover:bg-action/90 disabled:opacity-50"
                disabled={createScenario.isPending || updateScenario.isPending}
                onClick={() => void submitEditor()}
              >
                {editor.scenarioId ? 'Save changes' : 'Create scenario'}
              </button>
              <button
                type="button"
                className="rounded-md border border-border-light px-4 py-2 text-body text-navy hover:bg-surface"
                onClick={() => setEditor(null)}
              >
                Cancel
              </button>
            </div>
          </div>
        </SectionCard>
      )}
        </div>

        <div className="xl:col-span-3 space-y-6">
      {/* Results */}
      {!analysis && (
        <SectionCard title="Analysis results" noPadding>
          <div className="px-6 py-12 text-center">
            <p className="text-body font-medium text-navy">
              Select scenarios and run analysis
            </p>
            <p className="mt-1 text-caption text-slate">
              Side-by-side results appear here — transient until you save them.
            </p>
          </div>
        </SectionCard>
      )}
      {analysis && (
        <SectionCard
          title="Analysis results"
          subtitle={`Computed ${analysis.computedAt.toLocaleTimeString()} · as of ${analysis.asOfDate.toLocaleDateString()} · results are not saved unless you save them`}
          noPadding
          footer={
            <div className="flex items-center gap-3">
              <input
                className="flex-1 rounded-md border border-border-light bg-transparent px-3 py-2 text-body text-navy"
                placeholder="Name this analysis…"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
              />
              <button
                type="button"
                className="rounded-md border border-border-light px-4 py-2 text-body font-medium text-navy hover:bg-surface disabled:opacity-50"
                disabled={!saveName.trim() || saveAnalysis.isPending}
                onClick={() => void persist()}
              >
                {saveAnalysis.isPending ? 'Saving…' : 'Save analysis'}
              </button>
            </div>
          }
        >
          <div className="overflow-x-auto">
            <table className="w-full text-body">
              <thead>
                <tr className="border-b border-border text-micro font-medium uppercase tracking-wider text-slate bg-surface">
                  <th className="px-5 py-3 text-left">Metric</th>
                  {analysis.results.map((result: ScenarioResultRead) => (
                    <th
                      key={`h:${result.kind}:${result.code}:${result.scenarioId ?? ''}`}
                      className={`px-5 py-3 text-right ${
                        result.status === 'failed' ? 'text-critical' : ''
                      }`}
                    >
                      {result.label}
                      {result.status === 'failed' ? ' ⚠' : ''}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border-light">
                {metrics.map((spec) => {
                  const first = analysis.results[0];
                  const firstValue = Number(first?.metrics?.[spec.key] ?? Number.NaN);
                  return (
                    <tr key={spec.key}>
                      <td className="px-5 py-3 font-medium text-navy">{spec.label}</td>
                      {analysis.results.map((result: ScenarioResultRead, index: number) => {
                        if (result.status === 'failed') {
                          return (
                            <td
                              key={`${spec.key}:${index}`}
                              className="px-5 py-3 text-right font-mono text-slate-light"
                            >
                              —
                            </td>
                          );
                        }
                        const value = Number(result.metrics?.[spec.key] ?? Number.NaN);
                        const showDelta =
                          index > 0 && Number.isFinite(value) && Number.isFinite(firstValue);
                        const delta = showDelta ? value - firstValue : null;
                        const adverse =
                          delta !== null &&
                          ((spec.higherIsBetter ?? true) ? delta < 0 : delta > 0);
                        return (
                          <td
                            key={`${spec.key}:${index}`}
                            className="px-5 py-3 text-right font-mono text-navy whitespace-nowrap"
                          >
                            {fmtMetric(spec, result.metrics?.[spec.key])}
                            {delta !== null && delta !== 0 && (
                              <span
                                className={`ml-2 text-micro ${
                                  adverse ? 'text-critical' : 'text-slate'
                                }`}
                              >
                                {spec.kind === 'pct'
                                  ? `${delta > 0 ? '+' : ''}${delta.toFixed(1)}`
                                  : `${delta > 0 ? '+' : ''}${((delta / Math.abs(firstValue)) * 100).toFixed(1)}%`}
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
                <tr>
                  <td className="px-5 py-3 font-medium text-navy">Status</td>
                  {analysis.results.map((result: ScenarioResultRead, index: number) => {
                    const statusValue = result.metricStatuses?.[primaryMetric];
                    return (
                      <td key={`status:${index}`} className="px-5 py-3 text-right">
                        {result.status === 'failed' ? (
                          <StatusPill tone="critical">Failed</StatusPill>
                        ) : statusValue ? (
                          <StatusPill tone={STATUS_TONE[statusValue] ?? 'slate'}>
                            {statusValue.toUpperCase()}
                          </StatusPill>
                        ) : (
                          <StatusPill tone="slate">OK</StatusPill>
                        )}
                      </td>
                    );
                  })}
                </tr>
              </tbody>
            </table>
          </div>
          {analysis.results.some((r: ScenarioResultRead) => r.status === 'failed') && (
            <div className="px-5 py-3 border-t border-border-light space-y-1">
              {analysis.results
                .filter((r: ScenarioResultRead) => r.status === 'failed')
                .map((r: ScenarioResultRead) => (
                  <p
                    key={`err:${r.kind}:${r.code}:${r.scenarioId ?? ''}`}
                    className="text-caption text-critical"
                  >
                    {r.label} — {r.errorCode}: {r.errorMessage}
                  </p>
                ))}
            </div>
          )}
        </SectionCard>
      )}

      {/* Saved analyses */}
      {savedAnalyses.data && savedAnalyses.data.analyses.length > 0 && (
        <SectionCard
          title="Saved analyses"
          subtitle="Snapshots you chose to keep — recomputed server-side at save time"
          noPadding
        >
          <ul className="divide-y divide-border-light">
            {savedAnalyses.data.analyses.map((entry) => (
              <li key={entry.id} className="px-5 py-3 flex items-center gap-4">
                <div className="min-w-0 flex-1">
                  <p className="text-body font-medium text-navy">{entry.name}</p>
                  <p className="text-caption text-slate">
                    {entry.scenarioCount} scenario{entry.scenarioCount === 1 ? '' : 's'} ·{' '}
                    {entry.createdAt.toLocaleString()}
                  </p>
                </div>
                <button
                  type="button"
                  className="text-caption text-slate hover:underline shrink-0"
                  onClick={() => void deleteAnalysis.mutateAsync(entry.id)}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        </SectionCard>
      )}
        </div>
      </div>
    </div>
  );
}
