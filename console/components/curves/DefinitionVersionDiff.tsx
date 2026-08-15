'use client';

import { useMemo, useState } from 'react';
import { Field, Select, VersionDiff } from '@/components/ui';
import type { DeskCurveDefinition } from '@/lib/api';

/**
 * The governed parameter surface of a curve-definition version — exactly the
 * knobs Track-2 dual control governs (DeskCurveDefinitionFields). Volatile /
 * identity columns (id, proposed_by, approved_at, created_at) are excluded so
 * the diff shows only what actually changed in the methodology, not audit noise.
 */
export function governedParams(def: DeskCurveDefinition): Record<string, unknown> {
  return {
    currency: def.currency,
    calendar_name: def.calendar_name,
    curve_kind: def.curve_kind,
    projection_index: def.projection_index,
    discount_curve_code: def.discount_curve_code,
    instrument_set_ref: def.instrument_set_ref,
    interpolation_method: def.interpolation_method,
    output_daycount: def.output_daycount,
    payment_frequency: def.payment_frequency,
    payment_interval_months: def.payment_interval_months,
    curve_frequency: def.curve_frequency,
    spot_lag_days: def.spot_lag_days,
    roll_convention: def.roll_convention,
    extrapolation_rule: def.extrapolation_rule,
    entitlement_tier: def.entitlement_tier,
    params: def.params,
  };
}

function optionLabel(def: DeskCurveDefinition): string {
  return `v${def.version} · ${def.status}`;
}

/**
 * Side-by-side parameter diff between any two versions of one curve code — the
 * governance gap the console lacked (there was no way to see what a proposed
 * version actually changed). Defaults to comparing the earliest against the
 * latest; both pickers are free.
 */
export function DefinitionVersionDiff({ versions }: { versions: DeskCurveDefinition[] }) {
  const sorted = useMemo(
    () => [...versions].sort((a, b) => a.version - b.version),
    [versions],
  );
  const [leftV, setLeftV] = useState<number>(sorted[0]?.version ?? 0);
  const [rightV, setRightV] = useState<number>(sorted[sorted.length - 1]?.version ?? 0);

  if (sorted.length < 2) {
    return (
      <p className="px-5 py-4 text-caption text-slate">
        A version diff needs at least two versions. Propose a new version to compare recipes.
      </p>
    );
  }

  const left = sorted.find((v) => v.version === leftV) ?? sorted[0];
  const right = sorted.find((v) => v.version === rightV) ?? sorted[sorted.length - 1];

  return (
    <div className="space-y-3 px-5 py-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Base version" className="w-40">
          <Select value={String(left.version)} onChange={(e) => setLeftV(Number(e.target.value))}>
            {sorted.map((v) => (
              <option key={v.id} value={v.version}>
                {optionLabel(v)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Compare version" className="w-40">
          <Select value={String(right.version)} onChange={(e) => setRightV(Number(e.target.value))}>
            {sorted.map((v) => (
              <option key={v.id} value={v.version}>
                {optionLabel(v)}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <VersionDiff
        left={{ label: optionLabel(left), value: governedParams(left) }}
        right={{ label: optionLabel(right), value: governedParams(right) }}
      />
    </div>
  );
}
