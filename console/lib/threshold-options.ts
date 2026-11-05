import type { TenantLiquidityThreshold } from './api';

export function liquidityThresholdOption(threshold: TenantLiquidityThreshold) {
  const current = `${threshold.threshold_pct.toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })}%`;
  return {
    id: threshold.id,
    label: `${threshold.bank_id} · ${threshold.threshold_code} · ${threshold.institution_class} (liquidity) · ${current}`,
    current,
  };
}
