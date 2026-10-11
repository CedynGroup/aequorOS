'use client';

/**
 * Typed macro-scenario builder (docs/stress.md §4 item 1). Authors a governed
 * scenario as macro-variable PATHS (Table 6 drivers × year, base + stress) plus a
 * typed shock-palette to seed severe-but-plausible presets — replacing the raw
 * key/value textarea. Units and validation per variable; saves a draft to the
 * governed library and can submit it for approval (maker-checker).
 *
 * Values are entered in display units (a percentage for `fraction` variables, a
 * raw level otherwise) and converted to the stored decimal on save.
 */

import { Fragment, type ReactNode, useMemo, useState } from 'react';
import { Plus, Trash2, Wand2 } from 'lucide-react';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import { ApiError } from '@/lib/api/client';
import { MACRO_GROUPS, MACRO_VARS, MACRO_VAR_BY_KEY, SCENARIO_TYPES, SEVERITIES } from './macro';
import type { MacroScenario, MacroScenarioCreate, MacroPathIn } from './types';
import {
  useCreateMacroScenario,
  useSubmitMacroScenario,
  useUpdateMacroScenario,
} from './hooks';

type Cell = { base: string; stress: string };
type Grid = Record<string, Record<number, Cell>>; // variable → year → cell (display units)

type Preset = {
  key: string;
  label: string;
  severity: 'mild' | 'moderate' | 'severe';
  /** variable → {baseDisplay, stressDisplay} applied flat across the horizon. */
  shocks: Record<string, { base: number; stress: number }>;
};

// Typed shock palette — severe-but-plausible Ghanaian downturn templates that
// seed the grid (display units). Editable after seeding; never a hidden path.
const PRESETS: Preset[] = [
  {
    key: 'mild_slowdown',
    label: 'Mild slowdown',
    severity: 'mild',
    shocks: {
      gdp_growth: { base: 4.0, stress: 2.5 },
      inflation: { base: 12, stress: 16 },
      interest_rate: { base: 28, stress: 30 },
      fx_usd_ghs: { base: 12, stress: 13 },
    },
  },
  {
    key: 'moderate_stress',
    label: 'Moderate stress',
    severity: 'moderate',
    shocks: {
      gdp_growth: { base: 4.0, stress: 0.5 },
      inflation: { base: 12, stress: 22 },
      policy_rate: { base: 27, stress: 32 },
      interest_rate: { base: 28, stress: 34 },
      fx_usd_ghs: { base: 12, stress: 15 },
      gog_yield: { base: 20, stress: 27 },
    },
  },
  {
    key: 'severe_downturn',
    label: 'Severe downturn (≥1 required)',
    severity: 'severe',
    shocks: {
      gdp_growth: { base: 4.0, stress: -3.0 },
      inflation: { base: 12, stress: 30 },
      unemployment: { base: 4, stress: 9 },
      policy_rate: { base: 27, stress: 35 },
      interest_rate: { base: 28, stress: 38 },
      gog_yield: { base: 20, stress: 32 },
      fx_usd_ghs: { base: 12, stress: 17 },
      cocoa_price: { base: 3500, stress: 2600 },
      gold_price: { base: 2300, stress: 1900 },
    },
  },
];

function gridFromScenario(scenario: MacroScenario): Grid {
  const grid: Grid = {};
  for (const p of scenario.paths) {
    const meta = MACRO_VAR_BY_KEY[p.variable];
    const toDisplay = (v: string) => (meta?.kind === 'fraction' ? String(Number(v) * 100) : v);
    if (!grid[p.variable]) grid[p.variable] = {};
    grid[p.variable][p.year_index] = { base: toDisplay(p.base_value), stress: toDisplay(p.stress_value) };
  }
  return grid;
}

export default function ScenarioBuilder({
  defaultBankId,
  editScenario,
  onSaved,
  onCancel,
}: {
  defaultBankId?: string;
  editScenario?: MacroScenario | null;
  onSaved?: (scenarioId: string) => void;
  onCancel?: () => void;
}) {
  const isEdit = Boolean(editScenario);
  const [code, setCode] = useState(editScenario?.code ?? '');
  const [name, setName] = useState(editScenario?.name ?? '');
  const [description, setDescription] = useState(editScenario?.description ?? '');
  const [scenarioType, setScenarioType] = useState(editScenario?.scenario_type ?? 'adverse');
  const [severity, setSeverity] = useState<string>(editScenario?.severity ?? 'moderate');
  const [horizon, setHorizon] = useState(editScenario?.horizon_years ?? 3);
  const [source, setSource] = useState(editScenario?.source ?? '');
  const [narrative, setNarrative] = useState(editScenario?.narrative ?? '');
  const [bankScoped, setBankScoped] = useState(Boolean(editScenario?.bank_id));
  const [grid, setGrid] = useState<Grid>(editScenario ? gridFromScenario(editScenario) : {});
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  const create = useCreateMacroScenario();
  const update = useUpdateMacroScenario();
  const submit = useSubmitMacroScenario();

  const years = useMemo(() => Array.from({ length: horizon }, (_, i) => i + 1), [horizon]);
  const includedVars = Object.keys(grid);

  const toggleVar = (key: string) => {
    setGrid((g) => {
      const next = { ...g };
      if (next[key]) delete next[key];
      else next[key] = Object.fromEntries(years.map((y) => [y, { base: '', stress: '' }]));
      return next;
    });
  };

  const setCell = (variable: string, year: number, field: 'base' | 'stress', value: string) => {
    setGrid((g) => ({
      ...g,
      [variable]: { ...g[variable], [year]: { ...(g[variable]?.[year] ?? { base: '', stress: '' }), [field]: value } },
    }));
  };

  const applyPreset = (preset: Preset) => {
    setSeverity(preset.severity);
    setGrid(() => {
      const next: Grid = {};
      for (const [variable, { base, stress }] of Object.entries(preset.shocks)) {
        next[variable] = Object.fromEntries(years.map((y) => [y, { base: String(base), stress: String(stress) }]));
      }
      return next;
    });
  };

  const buildPaths = (): MacroPathIn[] => {
    const paths: MacroPathIn[] = [];
    for (const variable of includedVars) {
      const meta = MACRO_VAR_BY_KEY[variable];
      const toStore = (v: string) => (meta?.kind === 'fraction' ? String(Number(v) / 100) : v);
      for (const year of years) {
        const cell = grid[variable]?.[year];
        if (!cell || cell.base.trim() === '' || cell.stress.trim() === '') continue;
        if (!Number.isFinite(Number(cell.base)) || !Number.isFinite(Number(cell.stress))) continue;
        paths.push({
          variable,
          year_index: year,
          base_value: toStore(cell.base.trim()),
          stress_value: toStore(cell.stress.trim()),
        });
      }
    }
    return paths;
  };

  const validate = (paths: MacroPathIn[]): string | null => {
    if (!isEdit && !/^[a-z0-9_]{1,60}$/.test(code)) return 'Code must be a lowercase slug (a-z, 0-9, underscore).';
    if (!name.trim()) return 'Name is required.';
    if (paths.length === 0) return 'Add at least one macro-variable path (base + stress).';
    if (!reason.trim()) return 'A change reason is required (governance).';
    return null;
  };

  const save = async (thenSubmit: boolean) => {
    setError(null);
    const paths = buildPaths();
    const message = validate(paths);
    if (message) {
      setError(message);
      return;
    }
    try {
      let scenarioId: string;
      if (isEdit && editScenario) {
        const payload = {
          name,
          description: description || null,
          scenario_type: scenarioType as MacroScenarioCreate['scenario_type'],
          severity: severity as MacroScenarioCreate['severity'],
          horizon_years: horizon,
          narrative: narrative || null,
          source: source || null,
          paths,
          reason: reason.trim(),
        };
        const result = await update.mutateAsync({ scenarioId: editScenario.id, payload });
        scenarioId = result.id;
      } else {
        const payload: MacroScenarioCreate = {
          code,
          name,
          description: description || null,
          scenario_type: scenarioType as MacroScenarioCreate['scenario_type'],
          severity: severity as MacroScenarioCreate['severity'],
          horizon_years: horizon,
          narrative: narrative || null,
          source: source || null,
          bank_id: bankScoped ? defaultBankId ?? null : null,
          paths,
          reason: reason.trim(),
        };
        const result = await create.mutateAsync(payload);
        scenarioId = result.id;
      }
      if (thenSubmit) {
        await submit.mutateAsync({ scenarioId, reason: reason.trim() });
      }
      onSaved?.(scenarioId);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.errorCode ?? e.code ?? 'error'}: ${e.message}` : 'Could not save the scenario.');
    }
  };

  const pending = create.isPending || update.isPending || submit.isPending;

  return (
    <SectionCard
      title={isEdit ? `Edit scenario · ${editScenario?.code}` : 'New macro scenario'}
      subtitle="Author macro-variable paths (Table 6 drivers) as base + stress over the horizon — governed, versioned, maker-checker"
      actions={<StatusPill tone="action">Builder</StatusPill>}
    >
      <div className="space-y-5">
        {/* Metadata */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {!isEdit && (
            <Field label="Code (slug)">
              <input className={inputCls} value={code} onChange={(e) => setCode(e.target.value)} placeholder="severe_ddep_replay" />
            </Field>
          )}
          <Field label="Name">
            <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="Severe domestic downturn" />
          </Field>
          <Field label="Scenario type">
            <select className={inputCls} value={scenarioType} onChange={(e) => setScenarioType(e.target.value as typeof scenarioType)}>
              {SCENARIO_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </Field>
          <Field label="Severity">
            <select className={inputCls} value={severity} onChange={(e) => setSeverity(e.target.value)}>
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="Horizon (years)">
            <input
              type="number"
              min={3}
              max={10}
              className={inputCls}
              value={horizon}
              onChange={(e) => setHorizon(Math.max(1, Math.min(10, Number(e.target.value) || 3)))}
            />
          </Field>
          <Field label="Source attribution">
            <input className={inputCls} value={source} onChange={(e) => setSource(e.target.value)} placeholder="BoG, IMF WEO, GSS" />
          </Field>
        </div>
        <Field label="Description">
          <input className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>

        {!isEdit && (
          <label className="flex items-center gap-2 text-caption text-slate">
            <input type="checkbox" className="h-4 w-4 accent-action" checked={bankScoped} onChange={(e) => setBankScoped(e.target.checked)} />
            Scope to this bank only (unchecked = org-wide / supervisory scenario)
          </label>
        )}

        {/* Shock palette */}
        <div>
          <p className="text-caption font-medium text-slate mb-2 flex items-center gap-1.5">
            <Wand2 size={14} /> Shock palette — seed a severe-but-plausible template
          </p>
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => applyPreset(p)}
                className="rounded-md border border-border-light px-3 py-1.5 text-caption font-medium text-navy hover:bg-surface"
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* Variable picker */}
        <div>
          <p className="text-caption font-medium text-slate mb-2">Macro variables</p>
          <div className="space-y-2">
            {MACRO_GROUPS.map((group) => (
              <div key={group.key} className="flex flex-wrap items-center gap-2">
                <span className="w-28 shrink-0 text-micro uppercase tracking-wider text-slate-light">{group.label}</span>
                {MACRO_VARS.filter((v) => v.group === group.key).map((v) => {
                  const on = Boolean(grid[v.key]);
                  return (
                    <button
                      key={v.key}
                      type="button"
                      onClick={() => toggleVar(v.key)}
                      className={`rounded-md border px-2.5 py-1 text-caption ${
                        on ? 'border-action bg-action-light text-action' : 'border-border-light text-slate hover:bg-surface'
                      }`}
                    >
                      {on ? null : <Plus size={11} className="inline mr-1" />}
                      {v.label}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        </div>

        {/* Path grid */}
        {includedVars.length > 0 && (
          <div className="overflow-x-auto border border-border-light rounded-md">
            <table className="w-full text-caption">
              <thead>
                <tr className="border-b border-border bg-surface text-micro uppercase tracking-wider text-slate">
                  <th className="px-3 py-2 text-left">Variable</th>
                  {years.map((y) => (
                    <th key={y} className="px-3 py-2 text-center" colSpan={2}>Year {y}</th>
                  ))}
                  <th className="px-2 py-2"></th>
                </tr>
                <tr className="border-b border-border-light bg-surface text-micro text-slate-light">
                  <th className="px-3 py-1 text-left">unit</th>
                  {years.map((y) => (
                    <Fragment key={y}>
                      <th className="px-2 py-1 text-right font-normal">base</th>
                      <th className="px-2 py-1 text-right font-normal">stress</th>
                    </Fragment>
                  ))}
                  <th></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-light">
                {includedVars.map((variable) => {
                  const meta = MACRO_VAR_BY_KEY[variable];
                  return (
                    <tr key={variable}>
                      <td className="px-3 py-2">
                        <div className="text-navy font-medium">{meta?.label ?? variable}</div>
                        <div className="text-micro text-slate-light">{meta?.unit}</div>
                      </td>
                      {years.map((y) => {
                        const cell = grid[variable]?.[y] ?? { base: '', stress: '' };
                        return (
                          <Fragment key={y}>
                            <td className="px-1 py-1">
                              <input
                                inputMode="decimal"
                                value={cell.base}
                                onChange={(e) => setCell(variable, y, 'base', e.target.value)}
                                className="w-16 rounded border border-border-light bg-transparent px-1.5 py-1 text-right tnum text-navy"
                                aria-label={`${variable} year ${y} base`}
                              />
                            </td>
                            <td className="px-1 py-1">
                              <input
                                inputMode="decimal"
                                value={cell.stress}
                                onChange={(e) => setCell(variable, y, 'stress', e.target.value)}
                                className="w-16 rounded border border-border-light bg-transparent px-1.5 py-1 text-right tnum text-critical"
                                aria-label={`${variable} year ${y} stress`}
                              />
                            </td>
                          </Fragment>
                        );
                      })}
                      <td className="px-2 py-1 text-right">
                        <button type="button" onClick={() => toggleVar(variable)} className="text-slate hover:text-critical" aria-label={`Remove ${variable}`}>
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <Field label="Narrative (assumptions rationale)">
          <textarea className={`${inputCls} min-h-20`} value={narrative} onChange={(e) => setNarrative(e.target.value)} />
        </Field>
        <Field label="Change reason (required — governance audit)">
          <input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this scenario / edit" />
        </Field>

        {error && <p className="text-caption text-critical">{error}</p>}

        <div className="flex flex-wrap gap-3">
          <button type="button" className="btn-primary px-4 py-2 text-body font-medium disabled:opacity-50" disabled={pending} onClick={() => void save(false)}>
            {pending ? 'Saving…' : isEdit ? 'Save draft' : 'Save as draft'}
          </button>
          <button
            type="button"
            className="rounded-md border border-border-light px-4 py-2 text-body font-medium text-navy hover:bg-surface disabled:opacity-50"
            disabled={pending}
            onClick={() => void save(true)}
          >
            Save &amp; submit for approval
          </button>
          {onCancel && (
            <button type="button" className="rounded-md px-4 py-2 text-body text-slate hover:bg-surface" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
      </div>
    </SectionCard>
  );
}

const inputCls =
  'mt-1 w-full rounded-md border border-border-light bg-transparent px-3 py-2 text-body text-navy';

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="text-caption text-slate">{label}</span>
      {children}
    </label>
  );
}
