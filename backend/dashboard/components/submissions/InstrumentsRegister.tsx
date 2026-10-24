"use client";

import Link from "next/link";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useBankContext, useModuleScope } from "@/components/shell/BankContext";
import { isHrefVisible } from "@/lib/modules";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useReturnTemplates } from "@/lib/api/hooks";
import {
  instrumentDate,
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
  const { bank } = useBankContext();
  const requestedBankId = useSearchParams().get("bankId");
  const bankId = requestedBankId ?? bank?.id;
  const scope = useModuleScope();
  const query = useReturnTemplates(bankId);

  const templates = (query.data?.templates ?? []).filter(
    (item) => !upcoming || isUpcomingRule(item.instrumentStatus),
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
        {bankId && (
          <p className="text-caption text-slate">Institution: {bankId}</p>
        )}
        <p className="text-body text-slate">
          {upcoming
            ? "Prepare for proposed and forthcoming rules. Draft dates are conditional on final publication. Preparation does not establish compliance or readiness for live filing."
            : "Legal status, commencement and source evidence for every registered rule and the returns and calculations it drives. Dates not established by the source are shown explicitly."}
        </p>
        <Link
          className="text-action underline"
          href={`${upcoming ? "/submissions/instruments" : "/submissions/upcoming"}${requestedBankId ? `?bankId=${encodeURIComponent(requestedBankId)}` : ""}`}
        >
          {upcoming
            ? "All regulatory instruments"
            : "Preparing for upcoming rules"}
        </Link>
        <QueryBoundary
          contained
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => void query.refetch()}
        >
          {templates.length === 0 && (
            <p className="text-body text-slate">
              {upcoming
                ? "No upcoming rules in the register."
                : "No instruments available."}
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
                className="card p-5 space-y-3 scroll-mt-6"
              >
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <h2 className="text-h3 text-navy">{item.title}</h2>
                  <InstrumentStatusBadge
                    status={item.instrumentStatus}
                    effectiveFrom={item.effectiveFrom}
                    citation={item.directiveCitation}
                    sourceUrl={item.sourceUrl}
                    code={item.code}
                  />
                </div>
                <p className="text-caption text-navy">
                  {item.instrumentStatus === "exposure_draft"
                    ? "Expected start if made final"
                    : "Effective date"}
                  : {effective ?? "Not established in the register"}
                </p>
                <p className="text-caption text-slate">
                  {item.directiveCitation}
                </p>
                {item.sourceUrl ? (
                  <a
                    className="text-action underline text-caption"
                    href={item.sourceUrl}
                  >
                    Read the source
                  </a>
                ) : (
                  <p className="text-caption text-slate">
                    Primary source link not recorded. The citation above
                    identifies the registered evidence.
                  </p>
                )}
                {item.commencementParameter && (
                  <div className="text-caption text-navy space-y-1">
                    <p>
                      {differs
                        ? "Governed date differs from the default."
                        : "Governed commencement date."}{" "}
                      Default: {defaultDate ?? "Not established"}. Applied to
                      this institution: {effective ?? "Not established"}.
                    </p>
                    <p>
                      Approved by:{" "}
                      {item.commencementApprovedBy ?? "Approval not recorded"}
                      {item.commencementApprovedAt
                        ? ` on ${instrumentDate(item.commencementApprovedAt)}`
                        : ""}
                      . Parameter: {item.commencementParameter}.
                    </p>
                    <p className="text-slate">
                      A governed reporting anchor does not finalise an exposure
                      draft.
                    </p>
                  </div>
                )}
                {!isRuleInForce(item) &&
                  capability &&
                  bankId === bank?.id &&
                  isHrefVisible(capability, scope) && (
                    <Link
                      className="text-action underline text-caption"
                      href={`${capability}?preparing=1`}
                    >
                      Open preparation workspace
                    </Link>
                  )}
                <dl className="text-caption text-navy space-y-1">
                  <div>
                    <dt className="inline font-medium">Return: </dt>
                    <dd className="inline">
                      {bankId === bank?.id ? (
                        <Link
                          className="text-action underline"
                          href={`/submissions/returns?code=${encodeURIComponent(item.code)}`}
                        >
                          {item.code}
                        </Link>
                      ) : (
                        item.code
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt className="inline font-medium">
                      Calculations and limits:{" "}
                    </dt>
                    <dd className="inline">
                      {item.calculations?.length
                        ? item.calculations.join(", ")
                        : "No calculation or limit mapping recorded"}
                    </dd>
                  </div>
                  <div>
                    <dt className="inline font-medium">Calculation engine: </dt>
                    <dd className="inline">
                      {item.generator} ({item.code})
                    </dd>
                  </div>
                </dl>
              </section>
            );
          })}
        </QueryBoundary>
      </PageContainer>
    </>
  );
}
