"use client";

/**
 * Settings — personal preferences and operational information:
 *   · Appearance — real theme toggle (ThemeProvider)
 *   · Your account — the signed-in account and its permanent signer identity
 *   · Data & compute — real service health, market-data connections, and the
 *     official-run schedule note (read-only)
 *   · About — engine versions and provenance from persisted regulatory runs
 */

import { useQuery } from "@tanstack/react-query";
import { Monitor, Moon, Sun } from "lucide-react";
import PageHeader from "@/components/ui/PageHeader";
import CurrentAccountPanel from "@/components/settings/CurrentAccountPanel";
import LegacyAccessAnchorRedirect from "@/components/settings/LegacyAccessAnchorRedirect";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import RunBadge from "@/components/ui/RunBadge";
import StatusPill, { type StatusTone } from "@/components/ui/StatusPill";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { useBankContext, useModuleScope } from "@/components/shell/BankContext";
import {
  useTheme,
  type ThemePreference,
} from "@/components/shell/ThemeProvider";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import {
  MODULE_LABELS,
  useLatestRunsByModule,
} from "@/components/reports/hooks";
import { apiBaseUrl, apiOrigin } from "@/lib/api/client";
import { useCashflowHistory, useMarketDataConnections } from "@/lib/api/hooks";
import { fmtRelative, labelize } from "@/lib/api/values";

/** Ping the risk-service liveness endpoint directly (outside the generated client). */
function useRiskServiceHealth() {
  return useQuery({
    queryKey: ["health", "risk-service"],
    queryFn: async () => {
      const healthUrl = `${apiOrigin}/api/health/live`;
      const response = await fetch(healthUrl, {
        signal: AbortSignal.timeout(4000),
      });
      if (!response.ok)
        throw new Error(`Health check failed (${response.status})`);
      return (await response.json()) as { status?: string };
    },
    retry: false,
    refetchInterval: 60_000,
  });
}

export default function SettingsPage() {
  const { bank } = useBankContext();

  return (
    <>
      <LegacyAccessAnchorRedirect />
      <PageHeader title="Settings" />

      <div className="px-8 py-6 grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        <div className="space-y-6">
          <AppearancePanel />
          <CurrentAccountPanel />
        </div>
        <DataComputePanel bankId={bank?.id} />
        <AboutPanel bankId={bank?.id} />
      </div>
    </>
  );
}

function AppearancePanel() {
  const { theme, setTheme } = useTheme();
  const options: {
    value: ThemePreference;
    label: string;
    Icon: typeof Sun;
  }[] = [
    { value: "dark", label: "Dark", Icon: Moon },
    { value: "light", label: "Light", Icon: Sun },
    { value: "system", label: "System", Icon: Monitor },
  ];
  return (
    <Card>
      <CardHeader
        title="Appearance"
        subtitle="Theme preference — synced to your profile"
      />
      <CardBody>
        <div
          role="radiogroup"
          aria-label="Theme"
          className="inline-flex items-center gap-1 p-1 rounded-md bg-surface border border-border-light"
        >
          {options.map(({ value, label, Icon }) => {
            const selected = theme === value;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => setTheme(value)}
                className={`inline-flex items-center gap-2 px-4 py-2 rounded text-caption font-medium transition-colors ${
                  selected
                    ? "bg-surface-raised text-navy shadow-subtle border border-border-light"
                    : "text-slate hover:text-navy"
                }`}
              >
                <Icon size={14} aria-hidden />
                {label}
              </button>
            );
          })}
        </div>
        <p className="mt-3 text-caption text-slate leading-relaxed">
          Both themes run on the same semantic tokens; printed reports always
          render in the light palette.
        </p>
      </CardBody>
    </Card>
  );
}

function DataComputePanel({ bankId }: { bankId: string | undefined }) {
  const health = useRiskServiceHealth();
  const canViewCashflow = useModuleScope().liquidityConfidentialView === true;
  // Tiny query against the cashflow proxy — 503 means the ML sidecar is offline.
  const sidecarProbe = useCashflowHistory(
    canViewCashflow ? bankId : undefined,
    30,
  );
  const connections = useMarketDataConnections(bankId);

  const riskServiceTone: StatusTone = health.isLoading
    ? "slate"
    : health.data?.status === "ok"
      ? "success"
      : "critical";
  const riskServiceStatus = health.isLoading
    ? "Checking…"
    : health.data?.status === "ok"
      ? "OK"
      : "Down";

  const sidecarTone: StatusTone = !canViewCashflow
    ? "slate"
    : sidecarProbe.isLoading
      ? "slate"
      : sidecarProbe.data
        ? "success"
        : "amber";
  const sidecarStatus = !canViewCashflow
    ? "Restricted"
    : sidecarProbe.isLoading
      ? "Checking…"
      : sidecarProbe.data
        ? "OK"
        : "Offline";

  const connectionRows = connections.data?.connections ?? [];
  const activeConnections = connectionRows.filter(
    (c) => c.status === "active",
  ).length;

  return (
    <Card>
      <CardHeader
        title="Data & compute"
        subtitle="Read-only view of the services and feeds behind this workspace"
      />
      <CardBody className="space-y-3">
        <div className="flex items-center justify-between gap-3 py-2 border-b border-border-light">
          <div className="min-w-0">
            <p className="text-body text-navy">Risk service API</p>
            <p className="text-caption text-slate font-mono truncate">
              {apiBaseUrl}
            </p>
          </div>
          <StatusPill tone={riskServiceTone} className="shrink-0">
            {riskServiceStatus}
          </StatusPill>
        </div>

        <div className="flex items-center justify-between gap-3 py-2 border-b border-border-light">
          <div className="min-w-0">
            <p className="text-body text-navy">Cash-flow ML sidecar</p>
            <p className="text-caption text-slate">
              LSTM daily forecasts — optional; the LCR forecasting page degrades
              gracefully
            </p>
          </div>
          <StatusPill tone={sidecarTone} className="shrink-0">
            {sidecarStatus}
          </StatusPill>
        </div>

        <div className="py-2 border-b border-border-light">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-body text-navy">Market data pulls</p>
              <p className="text-caption text-slate">
                Vendor connections managed in the Data Engine
              </p>
            </div>
            {connections.isLoading ? (
              <SkeletonLine width={64} height={18} />
            ) : (
              <StatusPill
                tone={activeConnections > 0 ? "success" : "slate"}
                className="shrink-0"
              >
                {activeConnections > 0
                  ? `${activeConnections} active`
                  : "None connected"}
              </StatusPill>
            )}
          </div>
          {connectionRows.length > 0 && (
            <ul className="mt-2 space-y-1">
              {connectionRows.map((connection) => (
                <li
                  key={connection.id}
                  className="flex items-center justify-between gap-3 text-caption"
                >
                  <span className="text-navy/85 truncate">
                    {connection.displayName}
                  </span>
                  <span className="text-slate shrink-0">
                    {labelize(connection.status)}
                    {connection.lastPullAt &&
                      ` · last pull ${fmtRelative(connection.lastPullAt)}`}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="py-2">
          <p className="text-body text-navy">Official-run schedule</p>
          <p className="mt-1 text-caption text-slate leading-relaxed">
            The live engine recomputes automatically on every ingestion.
            Immutable official runs are minted on demand from each module
            dashboard (or the pipeline actions) — typically at period close,
            ahead of BSD filings.
          </p>
        </div>
      </CardBody>
    </Card>
  );
}

function AboutPanel({ bankId }: { bankId: string | undefined }) {
  const { query, byModule } = useLatestRunsByModule(bankId);
  const entries = Array.from(byModule.entries());
  const latest = entries
    .map(([, run]) => run)
    .sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime())[0];

  return (
    <Card>
      {/* flex-wrap lets the RunBadge chip drop below the title block instead of
          overlapping it when the card is narrow (two-column settings grid). */}
      <CardHeader
        className="flex-wrap"
        title="About"
        subtitle="Engine versions from the persisted regulatory runs"
        action={latest ? <RunBadge run={latest} /> : undefined}
      />
      <CardBody className="p-0">
        {query.isLoading ? (
          <div className="p-5 space-y-3">
            <SkeletonLine width="60%" />
            <SkeletonLine width="45%" />
            <SkeletonLine width="52%" />
          </div>
        ) : entries.length === 0 ? (
          <p className="px-5 py-4 text-body text-slate">
            No successful runs yet — engine versions appear here once the first
            module run is persisted.
          </p>
        ) : (
          <ul className="divide-y divide-border-light">
            {entries.map(([module, run]) => (
              <li
                key={module}
                className="px-5 py-2.5 flex items-center justify-between gap-3"
              >
                <span className="text-body text-navy">
                  {MODULE_LABELS[module] ?? labelize(module)} engine
                </span>
                <span className="font-mono text-caption text-slate tnum">
                  {run.engineVersion}
                </span>
              </li>
            ))}
          </ul>
        )}
        <div className="px-5 py-3 border-t border-border-light bg-surface/60">
          <p className="text-caption text-slate leading-relaxed">
            Every calculation persists an immutable run with engine version and
            input hash · regulatory math executes server-side only · identical
            inputs reproduce identical outputs.
          </p>
        </div>
      </CardBody>
    </Card>
  );
}
