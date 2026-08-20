'use client';

/**
 * Scenario Workbench — the enterprise stress-testing surface (docs/stress.md
 * Phase 6). Rebuilt from the old one-table shock sandbox into a real workbench:
 * a governed macro-scenario library + typed builder, an enterprise run that
 * drives every engine into a 3-year Appendix II projection, projection charts
 * that LEAD the surface, driver attribution, designated-base deltas, a
 * re-openable versioned run registry, N-way comparison + drill-down,
 * management-action with/without, and a board-pack composer.
 *
 * The component now delegates to `EnterpriseStressWorkbench`. The legacy props
 * (`module`, `metrics`, `primaryMetric`) are retained for source compatibility
 * with the module "scenarios/stress" route pages — only `module` is used, as a
 * lens label; the run itself is enterprise-wide.
 */

import EnterpriseStressWorkbench, {
  type StressModuleLens,
} from '@/components/stress/EnterpriseStressWorkbench';

export type MetricSpec = {
  higherIsBetter?: boolean;
  key: string;
  label: string;
  kind: 'pct' | 'ghs' | 'number';
};

type Props = {
  module: StressModuleLens;
  /** Legacy — retained for route-page source compatibility; unused. */
  metrics?: MetricSpec[];
  /** Legacy — retained for route-page source compatibility; unused. */
  primaryMetric?: string;
};

export default function ScenarioWorkbench({ module }: Props) {
  return <EnterpriseStressWorkbench moduleLens={module} />;
}
