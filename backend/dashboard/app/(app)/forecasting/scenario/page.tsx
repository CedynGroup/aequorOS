'use client';

import { useMemo, useState } from 'react';
import { Loader2, PlayCircle, RotateCcw } from 'lucide-react';
import type {
  ForecastPresetCode,
  ForecastScenarioListRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import StatusPill from '@/components/ui/StatusPill';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import ForecastRunView from '@/components/forecasting/ForecastRunView';
import { useBankContext } from '@/components/shell/BankContext';
import { useCreateForecastRun, useForecastScenarios } from '@/lib/api/hooks';
import { fmtDateUTC, num } from '@/lib/api/values';
import { fmtPctSigned } from '@/lib/format';

const PRESET_LABELS: Record<string, string> = {
  base: 'Base case',
  adverse: 'Adverse',
  severely_adverse: 'Severely adverse',
};

type FieldKey =
  | 'loanGrowthPct'
  | 'depositGrowthPct'
  | 'nimPct'
  | 'costToIncomePct'
  | 'creditLossRatePct'
  | 'fxDepreciationPct'
  | 'dividendPayoutPct'
  | 'feeIncomePctAssets'
  | 'taxRatePct'
  | 'securitiesShiftPp';

type FieldConfig = {
  key: FieldKey;
  apiKey: string;
  label: string;
  hint: string;
  min: number;
  max: number;
  step: number;
  unit: string;
};

const FIELDS: FieldConfig[] = [
  {
    key: 'loanGrowthPct',
    apiKey: 'loan_growth_pct',
    label: 'Loan growth',
    hint: 'Annual gross loan book growth',
    min: -20,
    max: 40,
    step: 0.5,
    unit: '%',
  },
  {
    key: 'depositGrowthPct',
    apiKey: 'deposit_growth_pct',
    label: 'Deposit growth',
    hint: 'Annual customer deposit growth',
    min: -20,
    max: 40,
    step: 0.5,
    unit: '%',
  },
  {
    key: 'nimPct',
    apiKey: 'nim_pct',
    label: 'Net interest margin',
    hint: 'NII as % of earning assets',
    min: 0,
    max: 12,
    step: 0.1,
    unit: '%',
  },
  {
    key: 'costToIncomePct',
    apiKey: 'cost_to_income_pct',
    label: 'Cost-to-income',
    hint: 'Operating expenses / total income',
    min: 20,
    max: 90,
    step: 0.5,
    unit: '%',
  },
  {
    key: 'creditLossRatePct',
    apiKey: 'credit_loss_rate_pct',
    label: 'Credit loss rate',
    hint: 'Annual provisions as % of gross loans',
    min: 0,
    max: 10,
    step: 0.1,
    unit: '%',
  },
  {
    key: 'fxDepreciationPct',
    apiKey: 'fx_depreciation_pct',
    label: 'FX depreciation',
    hint: 'Annual cedi depreciation applied to FX-linked RWAs',
    min: -10,
    max: 60,
    step: 1,
    unit: '%',
  },
  {
    key: 'dividendPayoutPct',
    apiKey: 'dividend_payout_pct',
    label: 'Dividend payout',
    hint: 'Share of net income distributed',
    min: 0,
    max: 100,
    step: 1,
    unit: '%',
  },
  {
    key: 'feeIncomePctAssets',
    apiKey: 'fee_income_pct_assets',
    label: 'Fee income',
    hint: 'Fees as % of total assets',
    min: 0,
    max: 5,
    step: 0.1,
    unit: '%',
  },
  {
    key: 'taxRatePct',
    apiKey: 'tax_rate_pct',
    label: 'Tax rate',
    hint: 'Effective corporate tax rate',
    min: 0,
    max: 50,
    step: 0.5,
    unit: '%',
  },
  {
    key: 'securitiesShiftPp',
    apiKey: 'securities_shift_pp',
    label: 'Securities shift',
    hint: 'Asset mix shift from loans into securities, percentage points',
    min: -20,
    max: 20,
    step: 0.5,
    unit: ' pp',
  },
];

type FormValues = Record<FieldKey, number>;

function presetValues(
  scenarios: ForecastScenarioListRead,
  preset: ForecastPresetCode
): FormValues | null {
  const found = scenarios.scenarios.find((s) => s.code === preset);
  if (!found) return null;
  const fromPreset = (apiKey: string, fallback: number): number => {
    const raw = found.assumptions[apiKey];
    return raw === undefined ? fallback : num(raw);
  };
  const defaults = scenarios.defaults;
  return {
    loanGrowthPct: fromPreset('loan_growth_pct', 0),
    depositGrowthPct: fromPreset('deposit_growth_pct', 0),
    nimPct: fromPreset('nim_pct', 0),
    costToIncomePct: fromPreset('cost_to_income_pct', 0),
    creditLossRatePct: fromPreset('credit_loss_rate_pct', 0),
    fxDepreciationPct: fromPreset('fx_depreciation_pct', 0),
    dividendPayoutPct: fromPreset('dividend_payout_pct', 0),
    feeIncomePctAssets: fromPreset(
      'fee_income_pct_assets',
      num(defaults.feeIncomePctAssets)
    ),
    taxRatePct: fromPreset('tax_rate_pct', num(defaults.taxRatePct)),
    securitiesShiftPp: fromPreset(
      'securities_shift_pp',
      num(defaults.securitiesShiftPp)
    ),
  };
}

export default function ScenarioBuilder() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const scenariosQuery = useForecastScenarios(bankId);
  const createRun = useCreateForecastRun(bankId);

  const [preset, setPreset] = useState<ForecastPresetCode>('base');
  const [overrides, setOverrides] = useState<Partial<FormValues>>({});

  const baseline = useMemo(
    () =>
      scenariosQuery.data ? presetValues(scenariosQuery.data, preset) : null,
    [scenariosQuery.data, preset]
  );
  const values: FormValues | null = baseline
    ? { ...baseline, ...overrides }
    : null;
  const touched = FIELDS.filter(
    (f) => baseline && values && values[f.key] !== baseline[f.key]
  );
  const isCustom = touched.length > 0;

  const setField = (key: FieldKey, value: number) => {
    setOverrides((prev) => ({ ...prev, [key]: value }));
  };

  const selectPreset = (code: ForecastPresetCode) => {
    setPreset(code);
    setOverrides({});
  };

  const submit = () => {
    if (!periodId || !values) return;
    if (isCustom) {
      createRun.mutate({
        reportingPeriodId: periodId,
        scenarioCode: 'custom',
        assumptions: {
          loanGrowthPct: values.loanGrowthPct,
          depositGrowthPct: values.depositGrowthPct,
          nimPct: values.nimPct,
          costToIncomePct: values.costToIncomePct,
          creditLossRatePct: values.creditLossRatePct,
          fxDepreciationPct: values.fxDepreciationPct,
          dividendPayoutPct: values.dividendPayoutPct,
          feeIncomePctAssets: values.feeIncomePctAssets,
          taxRatePct: values.taxRatePct,
          securitiesShiftPp: values.securitiesShiftPp,
        },
      });
    } else {
      createRun.mutate({
        reportingPeriodId: periodId,
        scenarioCode: preset,
      });
    }
  };

  const result = createRun.data;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting', href: '/forecasting' },
          { label: 'Scenario Builder' },
        ]}
        title="Scenario Builder"
        subtitle="Set the 10 forecast assumptions and run a persisted 5-year projection"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <button
            type="button"
            disabled={createRun.isPending || !periodId || !values}
            onClick={submit}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700 disabled:opacity-60"
          >
            {createRun.isPending ? (
              <Loader2 size={13} className="animate-spin" aria-hidden />
            ) : (
              <PlayCircle size={13} aria-hidden />
            )}
            Run {isCustom ? 'custom scenario' : PRESET_LABELS[preset]}
          </button>
        }
      />

      <QueryBoundary
        isLoading={scenariosQuery.isLoading}
        error={scenariosQuery.error}
        onRetry={() => scenariosQuery.refetch()}
      >
        {values && (
          <div className="px-8 py-6 space-y-6">
            {/* Preset selector */}
            <div className="card px-5 py-4 flex items-center gap-4 flex-wrap">
              <p className="text-micro font-medium uppercase tracking-wider text-slate">
                Start from preset
              </p>
              <div className="inline-flex gap-1 bg-surface p-1 rounded">
                {(scenariosQuery.data?.scenarios ?? []).map((s) => (
                  <button
                    key={s.code}
                    type="button"
                    onClick={() => selectPreset(s.code)}
                    className={`px-3 py-1.5 rounded text-caption font-medium ${
                      preset === s.code && !isCustom
                        ? 'bg-navy text-white'
                        : 'text-slate hover:text-navy'
                    }`}
                  >
                    {PRESET_LABELS[s.code] ?? s.code}
                  </button>
                ))}
              </div>
              {isCustom && (
                <span className="inline-flex items-center gap-2">
                  <StatusPill tone="action">
                    Custom — {touched.length} of {FIELDS.length} assumptions
                    changed
                  </StatusPill>
                  <button
                    type="button"
                    onClick={() => setOverrides({})}
                    className="inline-flex items-center gap-1 text-caption font-medium text-slate hover:text-navy"
                  >
                    <RotateCcw size={12} aria-hidden />
                    Reset to {PRESET_LABELS[preset]}
                  </button>
                </span>
              )}
            </div>

            {/* Assumption sliders */}
            <Card>
              <CardHeader
                title="Scenario assumptions"
                subtitle={`Prefilled from the ${PRESET_LABELS[preset]} preset — adjust any field to run a custom scenario`}
              />
              <CardBody className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-6">
                {FIELDS.map((field) => (
                  <AssumptionField
                    key={field.key}
                    field={field}
                    value={values[field.key]}
                    presetValue={baseline ? baseline[field.key] : 0}
                    onChange={(v) => setField(field.key, v)}
                  />
                ))}
              </CardBody>
            </Card>

            {/* Variance strip vs preset */}
            {isCustom && baseline && (
              <div className="card px-5 py-4">
                <p className="text-micro font-medium uppercase tracking-wider text-slate mb-3">
                  Variance vs {PRESET_LABELS[preset]} preset
                </p>
                <div className="flex items-center gap-x-8 gap-y-3 flex-wrap">
                  {touched.map((field) => {
                    const delta = values[field.key] - baseline[field.key];
                    return (
                      <div key={field.key}>
                        <p className="text-caption text-slate">{field.label}</p>
                        <p
                          className={`font-mono text-body font-medium tabular-nums ${
                            delta >= 0 ? 'text-success' : 'text-warning'
                          }`}
                        >
                          {fmtPctSigned(delta, 1).replace('%', field.unit.trim())}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {createRun.error && (
              <ErrorPanel error={createRun.error} title="Forecast run failed" />
            )}

            {/* Result */}
            {result && result.status === 'succeeded' ? (
              <>
                <div className="flex items-center gap-3">
                  <StatusPill tone="action">
                    {result.scenarioCode === 'custom'
                      ? 'Custom scenario'
                      : `${PRESET_LABELS[result.scenarioCode] ?? result.scenarioCode} scenario`}
                  </StatusPill>
                  <p className="text-caption text-slate">
                    Projection from the resolved assumptions persisted on the
                    run — the full record is available in the forecast run
                    history.
                  </p>
                </div>
                <ForecastRunView run={result} />
              </>
            ) : result ? (
              <ErrorPanel
                error={
                  new Error(
                    result.error?.message ??
                      'The forecast run did not complete successfully.'
                  )
                }
                title="Run failed"
              />
            ) : (
              <p className="text-caption text-slate">
                Run the scenario to see the projected 5-year path, regulatory
                ratios, and validations here.
              </p>
            )}
          </div>
        )}
      </QueryBoundary>
    </>
  );
}

function AssumptionField({
  field,
  value,
  presetValue,
  onChange,
}: {
  field: FieldConfig;
  value: number;
  presetValue: number;
  onChange: (value: number) => void;
}) {
  const changed = value !== presetValue;
  return (
    <div>
      <label className="block text-micro font-medium uppercase tracking-wider text-slate mb-2">
        {field.label}{' '}
        <span className={`font-mono ${changed ? 'text-action' : 'text-navy'}`}>
          {value}
          {field.unit}
        </span>
      </label>
      <div className="flex items-center gap-3">
        <input
          type="range"
          min={field.min}
          max={field.max}
          step={field.step}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="w-full accent-action"
          aria-label={field.label}
        />
        <input
          type="number"
          min={field.min}
          max={field.max}
          step={field.step}
          value={value}
          onChange={(e) => {
            const parsed = parseFloat(e.target.value);
            if (Number.isFinite(parsed)) {
              onChange(Math.min(field.max, Math.max(field.min, parsed)));
            }
          }}
          className="w-20 shrink-0 px-2 py-1 text-caption font-mono text-navy border border-border rounded tabular-nums"
          aria-label={`${field.label} value`}
        />
      </div>
      <div className="flex justify-between text-caption text-slate mt-1">
        <span className="font-mono">
          {field.min}
          {field.unit}
        </span>
        <span className="truncate px-2">{field.hint}</span>
        <span className="font-mono">
          {field.max}
          {field.unit}
        </span>
      </div>
    </div>
  );
}
