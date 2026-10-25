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
  instrumentIssuer,
  instrumentDisclosure,
  readableCalculation,
  isUpcomingRule,
  isRuleInForce,
} from "@/lib/regulatoryInstruments";
import InstrumentStatusBadge from "./InstrumentStatusBadge";

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
  const templates = allTemplates.filter(
    (item) =>
      (!upcoming || isUpcomingRule(item.instrumentStatus)) &&
      (!status || item.instrumentStatus === status) &&
      item.title
        .toLocaleLowerCase()
        .includes(search.trim().toLocaleLowerCase()),
  );
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
        <div className="flex flex-wrap gap-4">
          <label className="relative flex-1 min-w-48 self-end text-caption text-navy">
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
          <label className="text-caption text-navy">
            Status
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="mt-1 block rounded-sm border border-border px-3 py-2"
            >
              <option value="">All statuses</option>
              {[
                "exposure_draft",
                "final_not_in_force",
                "in_force",
                "unpublished",
              ]
                .filter(
                  (value) =>
                    !upcoming ||
                    value === "exposure_draft" ||
                    value === "final_not_in_force",
                )
                .map((value) => (
                  <option key={value} value={value}>
                    {instrumentDisclosure(value).label}
                  </option>
                ))}
            </select>
          </label>
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
          className="hidden lg:grid lg:grid-cols-[2fr_1fr_1.5fr_1fr_1.5fr] gap-4 px-4 text-caption font-medium text-slate"
          aria-hidden="true"
        >
          <span>Instrument</span>
          <span>Status</span>
          <span>Effective / expected date</span>
          <span>Issuer</span>
          <span>Linked return</span>
        </div>
        <QueryBoundary
          contained
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => void query.refetch()}
        >
          {templates.length === 0 && (
            <p className="text-body text-slate">
              {upcoming && !search && !status
                ? "No upcoming rules in the register."
                : "No instruments match your search and status filter."}
            </p>
          )}
          {templates.map((item) => {
            const effective = instrumentDate(item.effectiveFrom);
            const defaultDate = instrumentDate(item.defaultEffectiveFrom);
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
                <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr_1.5fr_1fr_1.5fr] items-start gap-4">
                  <div>
                    <h2 className="text-body font-medium text-navy">
                      {item.title}
                    </h2>
                    {item.instrumentStatus === "in_force" &&
                      /draft/i.test(item.directiveCitation ?? "") && (
                        <p className="mt-1 text-caption text-warning">
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
                  <p className="text-caption text-navy">
                    {effective ? (
                      <>
                        {item.instrumentStatus === "exposure_draft"
                          ? "Expected start if made final"
                          : "Effective date"}
                        : {effective}
                      </>
                    ) : item.instrumentStatus === "unpublished" ? (
                      "No filing commencement date"
                    ) : (
                      `Start date not published by ${instrumentIssuer(item.regulator || "the regulator")}`
                    )}
                  </p>
                  <p className="text-caption text-slate">
                    {instrumentIssuer(item.regulator || "Issuer not recorded")}
                  </p>
                  {bankId === bank?.id ? (
                    <Link
                      className="text-caption text-action underline"
                      href={`/submissions/returns?code=${encodeURIComponent(item.code)}`}
                    >
                      {item.title}
                    </Link>
                  ) : (
                    <p className="text-caption text-slate">{item.title}</p>
                  )}
                </div>
                <details className="text-caption">
                  <summary className="cursor-pointer text-action">
                    Citation and calculations
                  </summary>
                  <div className="mt-3 space-y-3">
                    {item.instrumentStatus === "in_force" &&
                      /draft/i.test(item.directiveCitation ?? "") && (
                        <p className="text-warning">
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
