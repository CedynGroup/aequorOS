"use client";

import Link from "next/link";
import { Info, Search } from "lucide-react";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useBankContext, useModuleScope } from "@/components/shell/BankContext";
import { isHrefVisible } from "@/lib/modules";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useReturnTemplates } from "@/lib/api/hooks";
import { isoDate } from "@/lib/api/values";
import {
  instrumentDate,
  instrumentDateLabel,
  instrumentIssuer,
  instrumentDisclosure,
  readableCalculation,
  isUpcomingRule,
  isRuleInForce,
  type InstrumentStatus,
} from "@/lib/regulatoryInstruments";
import InstrumentStatusBadge from "./InstrumentStatusBadge";

/** Status order follows the rule lifecycle: binding rules, then drafts. */
const REGISTER_STATUSES: InstrumentStatus[] = [
  "in_force",
  "final_not_in_force",
  "exposure_draft",
  "unpublished",
];
const UPCOMING_STATUSES: InstrumentStatus[] = [
  "exposure_draft",
  "final_not_in_force",
];
const ROW_GRID =
  "lg:grid-cols-[minmax(0,2fr)_minmax(8rem,0.9fr)_minmax(14rem,1.3fr)_minmax(6rem,0.7fr)_minmax(5rem,0.6fr)]";

/**
 * The commencement line states one of three date positions in the same column:
 * a recorded effective date, an expected date for a draft, or that no filing
 * date has been published.
 */
function commencement(item: {
  instrumentStatus?: InstrumentStatus;
  effectiveFrom?: Parameters<typeof instrumentDate>[0];
  regulator: string;
}): { label: string; date: string | null; tooltip?: string } {
  const recorded = instrumentDate(item.effectiveFrom);
  if (recorded) {
    const date = instrumentDateLabel(recorded);
    if (item.instrumentStatus === "exposure_draft")
      return {
        label: "Draft, expected",
        date,
        tooltip: `Expected from ${date} if made final.`,
      };
    if (item.instrumentStatus === "final_not_in_force")
      return { label: "Final, effective", date };
    return { label: "Effective", date };
  }
  if (item.instrumentStatus === "unpublished")
    return { label: "No filing date", date: null };
  return {
    label: `Start date not published by ${instrumentIssuer(item.regulator || "the regulator")}`,
    date: null,
  };
}

/** A status count that is also the filter, so one click narrows the table. */
function StatusChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={`${label}: ${count}`}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-caption font-medium transition-colors ${
        active
          ? "border-action bg-action-light text-action"
          : "border-border bg-surface-raised text-slate hover:text-navy"
      }`}
    >
      <span className="whitespace-nowrap">{label}</span>
      <span
        className={`rounded-full px-1.5 tabular-nums ${
          active ? "bg-action text-white" : "bg-slate-100 text-slate"
        }`}
      >
        {count}
      </span>
    </button>
  );
}

export default function InstrumentsRegister(props: { upcoming?: boolean }) {
  return (
    <Suspense>
      <RegisterContents {...props} />
    </Suspense>
  );
}

function RegisterContents({ upcoming = false }: { upcoming?: boolean }) {
  const { bank, banks } = useBankContext();
  const requestedBankId = useSearchParams().get("bankId");
  const bankId = requestedBankId ?? bank?.id;
  const institution =
    bankId === bank?.id ? bank : banks?.find((item) => item.id === bankId);
  const scope = useModuleScope();
  const query = useReturnTemplates(bankId);

  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const allTemplates = query.data?.templates ?? [];
  const today = isoDate(new Date());
  const horizon = new Date(`${today}T00:00:00Z`);
  horizon.setUTCDate(horizon.getUTCDate() + 90);
  const horizonDate = isoDate(horizon);
  const commencingCount = allTemplates.filter((item) => {
    const effective = instrumentDate(item.effectiveFrom);
    return (
      item.instrumentStatus === "final_not_in_force" &&
      effective !== null &&
      effective > today &&
      effective <= horizonDate
    );
  }).length;
  const templates = allTemplates
    .filter(
      (item) =>
        (!upcoming || isUpcomingRule(item.instrumentStatus)) &&
        (!status || item.instrumentStatus === status) &&
        item.title
          .toLocaleLowerCase()
          .includes(search.trim().toLocaleLowerCase()),
    )
    .sort((left, right) => {
      const rank = (value: InstrumentStatus | undefined) =>
        value && REGISTER_STATUSES.includes(value)
          ? REGISTER_STATUSES.indexOf(value)
          : REGISTER_STATUSES.length;
      return rank(left.instrumentStatus) - rank(right.instrumentStatus);
    });
  const registered = allTemplates.filter(
    (item) => !upcoming || isUpcomingRule(item.instrumentStatus),
  );
  const statusCounts = new Map<InstrumentStatus, number>();
  for (const item of registered) {
    if (!item.instrumentStatus) continue;
    statusCounts.set(
      item.instrumentStatus,
      (statusCounts.get(item.instrumentStatus) ?? 0) + 1,
    );
  }
  const statusChips = REGISTER_STATUSES.filter(
    (value) =>
      (!upcoming || UPCOMING_STATUSES.includes(value)) &&
      (statusCounts.get(value) ?? 0) > 0,
  );
  const filtered = Boolean(search.trim()) || Boolean(status);
  return (
    <>
      <PageHeader
        eyebrow="Regulatory reporting"
        title={
          upcoming ? "Preparing for upcoming rules" : "Regulatory instruments"
        }
      />
      <PageContainer className="py-6 space-y-4">
        {institution?.name && (
          <p className="text-caption text-slate">
            Institution: {institution.name}
          </p>
        )}
        <p className="text-body text-slate">
          {upcoming
            ? "Prepare for proposed and forthcoming rules. Draft dates are conditional on final publication. Preparation does not establish compliance or readiness for live filing."
            : "Legal status, commencement and source evidence for every registered rule and the returns and calculations it drives. Dates not established by the source are shown explicitly."}
        </p>
        {upcoming && (
          <Link
            className="text-action underline"
            href={`/submissions/instruments${requestedBankId ? `?bankId=${encodeURIComponent(requestedBankId)}` : ""}`}
          >
            All regulatory instruments
          </Link>
        )}
        <div className="flex flex-wrap items-end gap-4">
          <label className="relative flex-1 min-w-48 text-caption text-navy">
            <span className="sr-only">Search instruments</span>
            <Search
              size={16}
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate"
            />
            <input
              type="search"
              placeholder="Search instruments"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="block w-full rounded-sm border border-border pl-9 pr-3 py-2"
            />
          </label>
          {filtered && (
            <button
              type="button"
              onClick={() => {
                setSearch("");
                setStatus("");
              }}
              className="py-2 text-caption font-medium text-action underline"
            >
              Clear filters
            </button>
          )}
        </div>
        <div
          className="flex flex-wrap items-center gap-2"
          role="group"
          aria-label="Filter by status"
        >
          <StatusChip
            label="All"
            count={registered.length}
            active={!status}
            onClick={() => setStatus("")}
          />
          {statusChips.map((value) => (
            <StatusChip
              key={value}
              label={instrumentDisclosure(value).label}
              count={statusCounts.get(value) ?? 0}
              active={status === value}
              onClick={() => setStatus(status === value ? "" : value)}
            />
          ))}
        </div>
        {!upcoming && !query.error && commencingCount > 0 && (
          <div
            role="note"
            className="card border-l-4 border-l-action bg-action-light/30 flex items-start gap-3 p-4"
          >
            <Info
              size={16}
              className="mt-0.5 shrink-0 text-slate"
              aria-hidden
            />
            <p className="min-w-0 text-body text-navy">
              {commencingCount}{" "}
              {commencingCount === 1 ? "rule commences" : "rules commence"} in
              the next 90 days.{" "}
              <Link
                className="text-action underline"
                href={`/submissions/upcoming${requestedBankId ? `?bankId=${encodeURIComponent(requestedBankId)}` : ""}`}
              >
                Prepare
              </Link>
            </p>
          </div>
        )}
        <div
          className={`hidden lg:grid ${ROW_GRID} gap-4 px-4 text-caption font-medium text-slate`}
          aria-hidden="true"
        >
          <span>Instrument</span>
          <span>Status</span>
          <span>Effective or expected date</span>
          <span>Issuer</span>
          <span>Frequency</span>
        </div>
        <QueryBoundary
          contained
          // isPending, not isLoading: until the bank context resolves the
          // query is enabled-but-idle, and the register must hold its
          // skeleton instead of flashing an empty register that would then
          // collapse into a second loading swap once the bank arrives.
          isLoading={query.isPending}
          error={query.error}
          onRetry={() => void query.refetch()}
        >
          {templates.length === 0 && (
            <p className="text-body text-slate">
              {upcoming && !search && !status
                ? "No upcoming rules in the register."
                : filtered && search && status
                  ? "No instruments match your search and status filter."
                  : filtered && search
                    ? "No instruments match your search."
                    : status
                      ? "No instruments match that status."
                      : "No instruments in the register."}
            </p>
          )}
          {templates.map((item) => {
            const effective = instrumentDate(item.effectiveFrom);
            const defaultDate = instrumentDate(item.defaultEffectiveFrom);
            const date = commencement(item);
            const differs =
              effective !== defaultDate && Boolean(item.commencementParameter);
            const capability =
              item.code === "LCR-NSFR" && scope.institutionClass !== "sdi"
                ? "/liquidity"
                : item.code === "LMT" || item.code === "SDI-LMT-MONTHLY"
                  ? "/liquidity/monitoring"
                  : item.code === "SDI-IRRBB-QUARTERLY"
                    ? "/irr"
                    : item.code === "IRRBB-PILOT"
                      ? "/irr/standardised"
                      : item.code === "ICAAP-REPORT"
                        ? "/icaap"
                        : null;
            return (
              <section
                id={item.code}
                key={item.code}
                className="border-b border-border px-4 py-3 space-y-3 scroll-mt-6"
              >
                <div
                  className={`grid grid-cols-1 ${ROW_GRID} items-start gap-4`}
                >
                  <div>
                    <h2 className="text-body font-medium text-navy">
                      {bankId === bank?.id ? (
                        <Link
                          className="text-action underline"
                          href={`/submissions/returns?code=${encodeURIComponent(item.code)}`}
                        >
                          {item.title}
                        </Link>
                      ) : (
                        item.title
                      )}
                    </h2>
                    {item.instrumentStatus === "in_force" &&
                      /draft/i.test(item.directiveCitation ?? "") && (
                        <p className="mt-1 text-caption text-warning-strong">
                          Provisional citation
                        </p>
                      )}
                  </div>
                  <InstrumentStatusBadge
                    status={item.instrumentStatus}
                    regulator={item.regulator}
                    effectiveFrom={item.effectiveFrom}
                    citation={item.directiveCitation}
                    sourceUrl={item.sourceUrl}
                    code={item.code}
                  />
                  <p
                    className={`text-caption text-navy${
                      date.date ? " whitespace-nowrap" : ""
                    }`}
                    title={date.tooltip}
                  >
                    {date.date ? `${date.label} ${date.date}` : date.label}
                  </p>
                  <p className="text-caption text-slate">
                    {instrumentIssuer(item.regulator || "Issuer not recorded")}
                  </p>
                  <p className="text-caption text-slate capitalize">
                    {item.frequency}
                  </p>
                </div>
                <details className="text-caption">
                  <summary className="cursor-pointer text-action">
                    Citation and calculations
                  </summary>
                  <div className="mt-3 space-y-3">
                    {item.instrumentStatus === "in_force" &&
                      /draft/i.test(item.directiveCitation ?? "") && (
                        <p className="text-warning-strong">
                          Provisional citation: the registered evidence is a
                          draft. The recorded legal status remains in force;
                          final source confirmation is needed.
                        </p>
                      )}
                    <p className="text-caption text-slate">
                      {item.directiveCitation}
                    </p>
                    {item.sourceUrl ? (
                      <a
                        className="block w-fit text-action underline text-caption"
                        href={item.sourceUrl}
                      >
                        Read the source
                      </a>
                    ) : null}
                    {item.commencementParameter && (
                      <div className="text-caption text-navy space-y-1">
                        <p>
                          {differs
                            ? "Governed date differs from the default."
                            : "Governed commencement date."}{" "}
                          Default: {defaultDate ?? "Not established"}. Applied
                          to this institution: {effective ?? "Not established"}.
                        </p>
                        <p>
                          Approved by:{" "}
                          {item.commencementApprovedBy ??
                            "Approval not recorded"}
                          {item.commencementApprovedAt
                            ? ` on ${instrumentDate(item.commencementApprovedAt)}`
                            : ""}
                          .
                        </p>
                        <p className="text-slate">
                          A governed reporting anchor does not finalise an
                          exposure draft.
                        </p>
                      </div>
                    )}
                    {!isRuleInForce(item) &&
                      capability &&
                      bankId === bank?.id &&
                      isHrefVisible(capability, scope) && (
                        <Link
                          className="block w-fit text-action underline text-caption"
                          href={`${capability}?preparing=1`}
                        >
                          Open preparation workspace
                        </Link>
                      )}
                    <dl className="text-caption text-navy space-y-1">
                      <div>
                        <dt className="inline font-medium">
                          Calculations and limits:{" "}
                        </dt>
                        <dd className="inline">
                          {item.calculations?.length
                            ? [
                                ...new Set(
                                  item.calculations.map(readableCalculation),
                                ),
                              ].join(", ")
                            : "No calculation or limit mapping recorded"}
                        </dd>
                      </div>
                    </dl>
                  </div>
                </details>
              </section>
            );
          })}
        </QueryBoundary>
      </PageContainer>
    </>
  );
}
