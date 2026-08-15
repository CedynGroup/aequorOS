'use client';

import type { ReactNode } from 'react';
import { InfoTip, KpiStat } from '@/components/ui';
import type { OverviewResponse } from '@/lib/api';

/**
 * The cockpit KPI row: six dense `KpiStat` tiles driven entirely by the ONE
 * `getOverview()` rollup — tenant fleet, live-metrics staleness, 24h ingestion
 * and job failures, connection health, and the desk approvals backlog. Every
 * tile carries a status edge-glow and an InfoTip explainer in its hint slot.
 */
export function OverviewKpis({ overview }: { overview: OverviewResponse }) {
  const { tenants, ingestion, jobs, connections, desk } = overview;

  const deskPending =
    desk.pending_determinations +
    desk.pending_curve_determinations +
    desk.pending_oe_assessments;

  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
      <KpiStat
        label="Tenants"
        value={tenants.total}
        hint={
          <KpiHint text={`${tenants.banks_live} live · ${tenants.banks_empty} empty`}>
            Every onboarded organization. A bank goes live on its first ingestion; an
            empty tenant is provisioned but has not ingested yet.
          </KpiHint>
        }
      />

      <KpiStat
        label="Stale freshness"
        value={tenants.stale_count}
        status={tenants.stale_count > 0 ? 'warn' : 'ok'}
        hint={
          <KpiHint text={`of ${tenants.total} tenants`}>
            Tenants whose live-metrics freshness summary reports at least one stale
            module.
          </KpiHint>
        }
      />

      <KpiStat
        label="Failed batches"
        value={ingestion.failed_24h}
        status={ingestion.failed_24h > 0 ? 'crit' : 'ok'}
        hint={
          <KpiHint text="past 24h">
            Ingestion batches that failed across all tenants in the last 24 hours.
          </KpiHint>
        }
      />

      <KpiStat
        label="Jobs failed"
        value={jobs.failed_24h}
        status={jobs.failed_24h > 0 ? 'crit' : 'ok'}
        hint={
          <KpiHint text={`${jobs.running} running`}>
            Background jobs that failed in the last 24h (headline), and how many are
            running right now.
          </KpiHint>
        }
      />

      <KpiStat
        label="Connections"
        value={connections.ok}
        unit="ok"
        status={connections.crit > 0 ? 'crit' : connections.warn > 0 ? 'warn' : 'ok'}
        hint={
          <KpiHint text={`${connections.warn} warn · ${connections.crit} crit`}>
            Data-engine connection health across the whole fleet.
          </KpiHint>
        }
      />

      <KpiStat
        label="Desk approvals"
        value={deskPending}
        status={deskPending > 0 ? 'warn' : 'ok'}
        hint={
          <KpiHint
            text={`${desk.pending_determinations} det · ${desk.pending_curve_determinations} curve · ${desk.pending_oe_assessments} OE`}
          >
            Determinations, curve determinations, and operating-environment assessments
            awaiting review or approval.
          </KpiHint>
        }
      />
    </div>
  );
}

/** Bottom-row hint: the breakdown text plus a focusable InfoTip explainer. */
function KpiHint({ text, children }: { text: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="tnum">{text}</span>
      <InfoTip label="Explain this metric" placement="bottom-end" width="w-64">
        {children}
      </InfoTip>
    </span>
  );
}
