"use client";

import {
  useBankRules,
  useInForceAlerts,
  useInForceLiveSummary,
} from "@/lib/api/regulatoryRules";

/**
 * Cross-module live status card. Reads the polled live-summary + alerts views
 * and shows, per module, the current headline metric, traffic-light status, and
 * when it was last computed — plus the open breach count. Everything here
 * updates on its own as the background pipeline recomputes; no run button.
 */

import QueryBoundary from "@/components/ui/QueryBoundary";
import { RadioTower } from "lucide-react";
import type { LiveModuleView } from "@aequoros/risk-service-api";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import StatusPill from "@/components/ui/StatusPill";
import { useBankContext } from "@/components/shell/BankContext";
import { fmtRelative, statusTone } from "@/lib/api/values";
import { LIVE_MODULE_LABELS, livePrimaryMetric } from "./moduleDisplay";

// Fixed module order so the tiles read the same on every page.
const MODULE_ORDER: LiveModuleView["module"][] = [
  "liquidity",
  "capital",
  "irr",
  "fx",
  "ftp",
  "rating",
  "forecast",
];

export default function LiveStatusCard() {
  const { bank } = useBankContext();
  const summary = useInForceLiveSummary(bank?.id);
  const alerts = useInForceAlerts(bank?.id);

  const rules = useBankRules(bank?.id);
  const modules = summary.data?.modules ?? [];
  const byModule = new Map(modules.map((m) => [m.module, m]));
  const total = alerts.data?.total ?? 0;
  const computedAt = summary.data?.computedAt ?? null;

  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <RadioTower size={15} className="text-action" aria-hidden />
            Live status
          </span>
        }
        subtitle={
          summary.error
            ? "Live metrics unavailable"
            : summary.isLoading
              ? "Loading live metrics…"
              : computedAt
                ? `Auto-recomputed on ingestion · updated ${fmtRelative(computedAt)}`
                : "Auto-recomputed on ingestion — upload data to populate"
        }
        action={
          alerts.isLoading ? (
            <StatusPill tone="slate">Checking rule status…</StatusPill>
          ) : alerts.error ? (
            <StatusPill tone="slate">Alerts unavailable</StatusPill>
          ) : total > 0 ? (
            <StatusPill tone="breach">
              {total} breach{total === 1 ? "" : "es"}
            </StatusPill>
          ) : (
            <StatusPill tone="slate">0 in-force alerts</StatusPill>
          )
        }
      />
      <CardBody>
        <QueryBoundary
          contained
          isLoading={summary.isLoading}
          error={summary.error}
          onRetry={() => void summary.refetch()}
          skeleton={
            <p
              className="text-body text-slate"
              aria-busy="true"
              aria-label="Loading live metrics"
            >
              Loading live metrics…
            </p>
          }
        >
          {modules.length === 0 ? (
            <p className="text-body text-slate leading-relaxed">
              No live metrics yet. Upload data in the Data Engine — the pipeline
              derives and computes every module automatically.
            </p>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-px bg-border-light rounded-sm overflow-hidden border border-border-light">
              {MODULE_ORDER.filter(rules.isModuleInForce).map((module) => {
                const view = byModule.get(module);
                const primary = view
                  ? livePrimaryMetric(module, view.metrics)
                  : null;
                return (
                  <div key={module} className="bg-surface-raised px-3 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-micro uppercase tracking-wider text-slate truncate">
                        {LIVE_MODULE_LABELS[module]}
                      </p>
                      {view && (
                        <StatusPill
                          tone={statusTone(view.status)}
                          className="shrink-0"
                        >
                          {view.status}
                        </StatusPill>
                      )}
                    </div>
                    <p className="mt-1 font-mono text-h3 text-navy tabular-nums">
                      {primary ? primary.value : "—"}
                    </p>
                    <p className="text-micro text-slate truncate">
                      {primary ? primary.label : "Not computed yet"}
                      {view?.computedAt
                        ? ` · ${fmtRelative(view.computedAt)}`
                        : ""}
                    </p>
                  </div>
                );
              })}
            </div>
          )}
        </QueryBoundary>
      </CardBody>
    </Card>
  );
}
