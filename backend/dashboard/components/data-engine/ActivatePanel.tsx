"use client";

/**
 * Data flow status + advanced pipeline actions.
 *
 * The normal path is automatic: uploads are ingested, derived, and recomputed
 * in the background, so this panel leads with a live status line (last
 * recompute time + whether a job is running) rather than a run button.
 *
 * The two explicit actions are demoted to an "Advanced" area:
 *   - "Recompute now"  → POST /refresh (live tier: derive + recompute live
 *     metrics/findings, no immutable run minted).
 *   - "Mint official run for filing" → POST /official-runs (immutable tier:
 *     the auditable 22-scenario + forecast runs).
 *
 * Both enqueue a job, poll it to completion, and report the outcome inline.
 */

import { useId, useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  Loader2,
  RefreshCw,
  Stamp,
} from "lucide-react";
import type { JobRead } from "@aequoros/risk-service-api";
import { useBankContext } from "@/components/shell/BankContext";
import DataAccessNotice from "@/components/ui/DataAccessNotice";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import StatusPill from "@/components/ui/StatusPill";
import { isApiError } from "@/lib/api/client";
import {
  useLiveSummary,
  useMintOfficialRun,
  useRefreshBankData,
} from "@/lib/api/hooks";
import { useIngestionBatches } from "@/lib/api/ingestion";
import { fmtRelative } from "@/lib/api/values";
import { formatDate } from "./shared";

function progressCount(job: JobRead | undefined, key: string): number | null {
  const value = job?.progress?.[key];
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === "object") return Object.keys(value).length;
  return null;
}

/** Mirror of live_view.MINT_RUN_ENGINES (#177 part B): each engine the bank's
 * regime actually runs (its entitlement set — the same source the backend's
 * runs_module consults) demands a confidential run sentence, resolved against
 * the module-scope flags below. The SDI substitution is mirrored too: an SDI's
 * liquidity is the LMTD monitoring view, not the official-run LCR/NSFR engine.
 * The service re-decides every mint; this only decides whether the control is
 * offered. */
const MINT_RUN_REQUIREMENTS = [
  { engine: "liquidity", moduleKey: "liquidity", label: "Liquidity", flag: "liquidityRun" },
  { engine: "capital", moduleKey: "capital", label: "Capital", flag: "capitalRun" },
  { engine: "rating", moduleKey: "markets", label: "Market Data", flag: "marketsRun" },
  { engine: "credit", moduleKey: "credit", label: "Credit", flag: "creditRun" },
  { engine: "irr", moduleKey: "irrbb", label: "IRRBB", flag: "irrbbRun" },
  { engine: "fx", moduleKey: "fx", label: "FX", flag: "fxRun" },
  { engine: "ftp", moduleKey: "ftp", label: "Funds Transfer Pricing", flag: "ftpRun" },
  { engine: "forecast", moduleKey: "forecasting", label: "Forecasting", flag: "forecastingRun" },
] as const;

const RECOMPUTE_REASON =
  "Recomputing the live tier needs DATA · Confidential · Run. Ask your organization owner for the grant.";

export default function ActivatePanel() {
  const dateId = useId();
  const { bank, period, moduleScope } = useBankContext();
  const batchesQuery = useIngestionBatches(bank?.id);
  const liveSummary = useLiveSummary(bank?.id);
  const refresh = useRefreshBankData(bank?.id);
  const mint = useMintOfficialRun(bank?.id);

  const [advancedOpen, setAdvancedOpen] = useState(false);

  // The recompute is a mutation of the derived live plane, not a read (#177
  // part B): the button demands DATA/confidential run, which the backend
  // enforces before anything is enqueued.
  const canRefresh = moduleScope.dataRun === true;
  const missingMintRuns = useMemo(() => {
    const entitled = moduleScope.entitledModules;
    if (!entitled) return [];
    return MINT_RUN_REQUIREMENTS.filter(
      (requirement) =>
        entitled.has(requirement.moduleKey) &&
        !(
          requirement.engine === "liquidity" &&
          moduleScope.institutionClass === "sdi"
        ) &&
        moduleScope[requirement.flag] !== true,
    );
  }, [moduleScope]);
  const canMint = missingMintRuns.length === 0;
  const mintReason =
    missingMintRuns.length > 0
      ? `Minting an official run needs confidential Run on every planned engine. Missing: ${missingMintRuns
          .map((requirement) => `${requirement.label} · Run`)
          .join(", ")}.`
      : null;

  const latestBatchAsOf = useMemo(() => {
    if (batchesQuery.access !== "allowed" || !batchesQuery.isSuccess)
      return undefined;
    const dates = (batchesQuery.data?.batches ?? [])
      .filter((batch) => batch.status.startsWith("accepted"))
      .map((batch) => formatDate(batch.asOfDate));
    return dates.sort().at(-1);
  }, [batchesQuery.access, batchesQuery.isSuccess, batchesQuery.data]);

  const periodAsOf = period ? formatDate(period.periodEnd) : undefined;
  const [asOfDate, setAsOfDate] = useState<string>("");
  const effectiveAsOf =
    asOfDate ||
    (batchesQuery.access === "allowed" && batchesQuery.isSuccess
      ? latestBatchAsOf || periodAsOf || ""
      : "");

  const computedAt = liveSummary.data?.computedAt ?? null;
  const refreshDelayed = !computedAt;
  const running = refresh.isPending || mint.isPending;

  return (
    <section className="card p-5 space-y-4">
      <div>
        <h2 className="text-h2 text-navy">Data flow</h2>
        <p className="mt-1 text-body text-slate leading-relaxed">
          Your data flows automatically. Uploads are ingested, derived, and
          recalculated in the background — every module dashboard reflects the
          latest data without a manual run.
        </p>
      </div>

      {/* Live status line */}
      <div className="rounded-sm border border-border-light bg-surface/60 px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        {running ? (
          <StatusPill tone="action">
            <Loader2 size={11} className="animate-spin" aria-hidden />
            Recomputing…
          </StatusPill>
        ) : refreshDelayed ? (
          <StatusPill tone="amber">Live refresh pending</StatusPill>
        ) : (
          <StatusPill tone="compliant">Up to date</StatusPill>
        )}
        <span className="text-body text-navy">
          Last recompute{" "}
          <span className="font-medium">
            {computedAt ? fmtRelative(computedAt) : "not yet"}
          </span>
          {liveSummary.data?.periodLabel && (
            <span className="text-slate">
              {" "}
              · period{" "}
              <span className="font-mono text-navy">
                {liveSummary.data.periodLabel}
              </span>
            </span>
          )}
        </span>
      </div>

      {/* Advanced actions — the manual pipeline controls, demoted */}
      <div className="border-t border-border-light pt-3">
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          aria-expanded={advancedOpen}
          className="inline-flex items-center gap-1.5 text-caption font-medium uppercase tracking-wider text-slate hover:text-navy"
        >
          <ChevronDown
            size={14}
            aria-hidden
            className={`transition-transform ${advancedOpen ? "rotate-180" : ""}`}
          />
          Advanced actions
        </button>

        {advancedOpen && (
          <div className="mt-4 space-y-4">
            <div>
              <label
                htmlFor={dateId}
                className="block text-caption font-medium text-slate mb-1"
              >
                As-of date
              </label>
              <input
                id={dateId}
                type="date"
                value={effectiveAsOf}
                onChange={(event) => setAsOfDate(event.target.value)}
                className="px-3 py-1.5 rounded-sm border border-border text-body text-navy font-mono"
              />
              {batchesQuery.access === "loading" || batchesQuery.isLoading ? (
                <p role="status" className="mt-1 text-caption text-slate">
                  Loading upload dates. You can enter a date manually.
                </p>
              ) : batchesQuery.access === "denied" ? (
                <>
                  <DataAccessNotice requirement="DATA · Confidential · View to read upload dates" />
                  <p className="mt-1 text-caption text-slate">
                    Enter an as-of date manually. The latest accepted upload
                    date is unavailable.
                  </p>
                </>
              ) : batchesQuery.isError ? (
                <>
                  <ErrorPanel
                    error={batchesQuery.error}
                    onRetry={() => void batchesQuery.refetch()}
                  />
                  <p className="mt-1 text-caption text-slate">
                    Upload dates are unavailable. Enter an as-of date manually.
                  </p>
                </>
              ) : (
                <p className="mt-1 text-micro text-slate">
                  {latestBatchAsOf
                    ? "Defaults to the latest accepted upload."
                    : periodAsOf
                      ? "No accepted uploads. Defaults to the selected reporting period."
                      : "No accepted uploads or reporting period. Enter an as-of date."}{" "}
                  Both actions run for this reporting date.
                </p>
              )}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              {/* Recompute now → /refresh */}
              <div className="rounded-sm border border-border-light p-4 space-y-2">
                <div className="flex items-center gap-2">
                  <RefreshCw size={15} className="text-action" aria-hidden />
                  <h3 className="text-body font-medium text-navy">
                    Recompute now
                  </h3>
                </div>
                <p className="text-caption text-slate leading-relaxed">
                  Re-derive facts and refresh live metrics and alerts. Does not
                  mint an immutable regulatory run.
                </p>
                {(() => {
                  const recomputeButton = (describedBy: string | undefined) => (
                    <button
                      type="button"
                      disabled={!bank || !effectiveAsOf || running || !canRefresh}
                      aria-describedby={describedBy}
                      onClick={() =>
                        refresh.mutate({
                          asOfDate: effectiveAsOf,
                          reason:
                            "Recompute now from the Data Engine console.",
                        })
                      }
                      className="inline-flex items-center gap-2 px-3 py-2 rounded-sm text-caption font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {refresh.isPending && (
                        <Loader2 size={14} className="animate-spin" aria-hidden />
                      )}
                      {refresh.isPending ? "Recomputing…" : "Recompute now"}
                    </button>
                  );
                  return canRefresh ? (
                    recomputeButton(undefined)
                  ) : (
                    <DisabledWithReason reason={RECOMPUTE_REASON}>
                      {recomputeButton}
                    </DisabledWithReason>
                  );
                })()}
                {!canRefresh && (
                  <p className="text-caption text-slate">
                    Recomputing needs DATA · Confidential · Run.
                  </p>
                )}
                <ActionResult
                  isError={refresh.isError}
                  error={refresh.error}
                  job={refresh.data}
                  successLabel={(job) => {
                    const ok = progressCount(job, "modules_ok");
                    const failed = progressCount(job, "modules_failed") ?? 0;
                    return `Recomputed${ok !== null ? ` ${ok} modules` : ""}${
                      failed > 0 ? ` · ${failed} failed` : ""
                    }`;
                  }}
                />
              </div>

              {/* Mint official run → /official-runs */}
              <div className="rounded-sm border border-border-light p-4 space-y-2">
                <div className="flex items-center gap-2">
                  <Stamp size={15} className="text-navy" aria-hidden />
                  <h3 className="text-body font-medium text-navy">
                    Mint official run for filing
                  </h3>
                </div>
                <p className="text-caption text-slate leading-relaxed">
                  Create the immutable, auditable regulatory runs (22 scenarios
                  + forecast) for this reporting date.
                </p>
                {(() => {
                  const mintButton = (describedBy: string | undefined) => (
                    <button
                      type="button"
                      disabled={!bank || !effectiveAsOf || running || !canMint}
                      aria-describedby={describedBy}
                      onClick={() =>
                        mint.mutate({
                          asOfDate: effectiveAsOf,
                          reason:
                            "Minted official run for filing from the Data Engine console.",
                        })
                      }
                      className="inline-flex items-center gap-2 px-3 py-2 rounded-sm text-caption font-medium btn-primary disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {mint.isPending && (
                        <Loader2 size={14} className="animate-spin" aria-hidden />
                      )}
                      {mint.isPending ? "Minting…" : "Mint official run"}
                    </button>
                  );
                  return canMint ? (
                    mintButton(undefined)
                  ) : (
                    <DisabledWithReason reason={mintReason ?? ""}>
                      {mintButton}
                    </DisabledWithReason>
                  );
                })()}
                {mintReason && (
                  <p className="text-caption text-slate">{mintReason}</p>
                )}
                <ActionResult
                  isError={mint.isError}
                  error={mint.error}
                  job={mint.data}
                  successLabel={(job) => {
                    const count = progressCount(job, "modules");
                    return `Official run minted${
                      count !== null ? ` · ${count} modules` : ""
                    }`;
                  }}
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function ActionResult({
  isError,
  error,
  job,
  successLabel,
}: {
  isError: boolean;
  error: unknown;
  job: JobRead | undefined;
  successLabel: (job: JobRead) => string;
}) {
  if (isError) {
    return (
      <p className="text-caption text-critical">
        {isApiError(error) ? error.message : "The job failed."}
      </p>
    );
  }
  if (job && job.status === "succeeded") {
    return (
      <p className="inline-flex items-center gap-1.5 text-caption text-success">
        <CheckCircle2 size={13} aria-hidden />
        {successLabel(job)}
      </p>
    );
  }
  return null;
}
