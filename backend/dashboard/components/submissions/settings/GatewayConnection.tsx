"use client";

/**
 * The bank's connection to the reporting gateway — AequorOS' own channel to
 * the regulator. Banks set nothing up here: AequorOS enrols each bank, which
 * creates its secure connection and a signature key that exists only for it.
 * This page shows that connection, lets the bank test it, and lists what has
 * been sent. Key material never reaches the browser.
 */

import Link from "next/link";
import { useId } from "react";
import {
  CheckCircle2,
  Circle,
  Loader2,
  PlugZap,
  ShieldCheck,
} from "lucide-react";
import type {
  ReportingGatewayStatus,
  FilingProfileRead,
  GatewayStepKey,
} from "@aequoros/risk-service-api";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill, { type StatusTone } from "@/components/ui/StatusPill";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonCard } from "@/components/ui/Skeleton";
import {
  useReportingGatewayStatus,
  useFilingProfile,
  useTestReportingGatewayConnection,
} from "@/lib/api/hooks";
import {
  useConfigureEligibility,
  useViewEligibility,
} from "@/lib/api/configureAccess";
import { fmtDateUTC, fmtTimestamp } from "@/lib/api/values";
import { centralBankName, regShort } from "@/lib/format";
import { SettingsHeading } from "./SettingsChrome";
import { DeniedSettings } from "./DeniedSettings";

const SECTION = "Submission channels";

const GATEWAY_STATES: Record<string, { label: string; tone: StatusTone }> = {
  received: { label: "Received", tone: "action" },
  validating: { label: "Validating", tone: "action" },
  in_review: { label: "In review", tone: "amber" },
  queried: { label: "Queried", tone: "amber" },
  accepted: { label: "Accepted", tone: "success" },
  rejected: { label: "Rejected", tone: "critical" },
  superseded: { label: "Superseded", tone: "slate" },
};

function gatewayState(state: string): { label: string; tone: StatusTone } {
  return GATEWAY_STATES[state] ?? { label: "Received", tone: "action" };
}

function when(value: string | null | undefined): string | null {
  return value ? fmtTimestamp(new Date(value)) : null;
}

const CERTIFICATE_WARNING_DAYS = 30;
const MS_PER_DAY = 86_400_000;

/** The connection certificate's standing on the day the page is read. */
function certificateState(validUntil: Date): {
  label: string;
  tone: StatusTone;
  detail: string;
} {
  const remaining = validUntil.getTime() - Date.now();
  if (remaining <= 0) {
    return {
      label: "Expired",
      tone: "critical",
      detail: `Expired on ${fmtDateUTC(validUntil)}`,
    };
  }
  if (remaining <= CERTIFICATE_WARNING_DAYS * MS_PER_DAY) {
    return {
      label: "Expires soon",
      tone: "amber",
      detail: `Valid until ${fmtDateUTC(validUntil)} · expires within ${CERTIFICATE_WARNING_DAYS} days`,
    };
  }
  return {
    label: "Valid",
    tone: "success",
    detail: `Valid until ${fmtDateUTC(validUntil)}`,
  };
}

function environmentLabel(environment: string | undefined): string {
  return environment === "production" ? "Production" : "Sandbox";
}

function title(): string {
  return "AequorOS Reporting Gateway";
}

export default function GatewayConnection({ bankId }: { bankId: string }) {
  const { allowed: canView, reason: viewReason } = useViewEligibility(
    bankId,
    "reg",
  );
  const statusQuery = useReportingGatewayStatus(bankId, { enabled: canView });
  const profileQuery = useFilingProfile(bankId, { enabled: canView });

  if (!canView) {
    return (
      <>
        <SettingsHeading title={title()} section={SECTION} />
        <DeniedSettings title="Gateway connection" reason={viewReason} />
      </>
    );
  }
  if (statusQuery.isPending) {
    return (
      <>
        <SettingsHeading title={title()} section={SECTION} />
        <SkeletonCard />
      </>
    );
  }
  if (statusQuery.error) {
    return (
      <>
        <SettingsHeading title={title()} section={SECTION} />
        <ErrorPanel
          error={statusQuery.error}
          onRetry={() => statusQuery.refetch()}
          title="Could not load the gateway connection"
        />
      </>
    );
  }
  const status = statusQuery.data;
  return status.enrolled ? (
    <Connected bankId={bankId} status={status} />
  ) : (
    <NotEnrolled profile={profileQuery.data} />
  );
}

/** The gateway identifies institutions by their four-digit institution code. */
const GATEWAY_CODE = /^\d{4}$/;

function NotEnrolled({ profile }: { profile: FilingProfileRead | undefined }) {
  const code = profile?.institutionCode ?? null;
  const checks = [
    {
      label: `${regShort()} institution code`,
      ready: code !== null && GATEWAY_CODE.test(code),
      detail:
        code === null
          ? "Add it to your institution profile"
          : GATEWAY_CODE.test(code)
            ? code
            : `${code} · the gateway needs the four-digit institution code`,
    },
    {
      label: "Reporting contact",
      ready: Boolean(profile?.contactName),
      detail: profile?.contactName ?? "Add it to your filing profile",
    },
  ];
  return (
    <>
      <SettingsHeading
        title={title()}
        section={SECTION}
        status={<StatusPill tone="slate">Not yet enrolled</StatusPill>}
      />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <SectionCard
          className="xl:col-span-2"
          title="Your AequorOS team enrols your bank"
        >
          <div className="max-w-2xl space-y-4">
            <p className="text-body leading-relaxed text-navy">
              The AequorOS Reporting Gateway is the channel AequorOS runs to
              deliver your returns to the {centralBankName()}. Enrolment is done
              for you, usually as part of onboarding. Once it is done, approved
              returns file from here, and there is nothing for you to install or
              set up.
            </p>
            <ol className="list-decimal space-y-2 pl-5 text-body leading-relaxed text-navy">
              <li>
                Your bank is registered on the gateway with the institution code
                in your institution profile.
              </li>
              <li>
                A secure connection and a signature key that exists only for
                your bank are created.
              </li>
              <li>
                A test confirms the connection, and this page shows the gateway
                as connected.
              </li>
            </ol>
            <p className="text-caption leading-relaxed text-slate">
              If your bank should already be enrolled, contact your AequorOS
              onboarding team.
            </p>
          </div>
        </SectionCard>
        <SectionCard title="Ready for enrolment">
          <ul className="space-y-3">
            {checks.map((check) => (
              <li key={check.label} className="flex items-start gap-2.5">
                {check.ready ? (
                  <CheckCircle2
                    size={16}
                    className="mt-0.5 shrink-0 text-success"
                    aria-hidden
                  />
                ) : (
                  <Circle
                    size={16}
                    className="mt-0.5 shrink-0 text-slate"
                    aria-hidden
                  />
                )}
                <span className="min-w-0">
                  <span className="block text-body font-medium text-navy">
                    {check.label}
                  </span>
                  <span className="block break-words text-caption text-slate">
                    {check.detail}
                  </span>
                </span>
              </li>
            ))}
          </ul>
          <Link
            href="/submissions/settings/filing-profile"
            className="mt-4 inline-block text-caption font-medium text-action hover:underline"
          >
            Edit filing profile
          </Link>
        </SectionCard>
      </div>
    </>
  );
}

const STEP_LABELS: Record<GatewayStepKey, string> = {
  enrolled: "Enrolled by AequorOS",
  signing_key_created: "Signature key created",
  connection_tested: "Connection tested",
  first_filing: "Filing live",
};

function stepDetail(
  key: GatewayStepKey,
  status: ReportingGatewayStatus,
): string {
  const latest = status.transmissions[0];
  switch (key) {
    case "enrolled":
      return [
        status.institutionCode
          ? `Institution code ${status.institutionCode}`
          : null,
        when(status.enrolledAt),
      ]
        .filter(Boolean)
        .join(" · ");
    case "signing_key_created":
      return status.signingKey?.kid ?? "";
    case "connection_tested":
      return when(status.lastConnectionTestAt) ?? "Not tested yet";
    case "first_filing":
      return latest
        ? `Latest filing ${fmtTimestamp(latest.sentAt)}`
        : "Sent with your first approved return";
  }
}

function Connected({
  bankId,
  status,
}: {
  bankId: string;
  status: ReportingGatewayStatus;
}) {
  const test = useTestReportingGatewayConnection(bankId);
  const { allowed: canConfigure, reason: configureReason } =
    useConfigureEligibility(bankId, "reg");
  const reasonId = useId();
  const connection = status.connection;
  const certificate = connection ? certificateState(connection.renewsBy) : null;
  const key = status.signingKey;
  const tested = when(status.lastConnectionTestAt);
  const testedLine = tested
    ? `Last tested ${tested} · ${status.lastConnectionTestOk ? "passed" : "failed"}`
    : "Not tested yet";

  return (
    <>
      <SettingsHeading
        title={title()}
        section={SECTION}
        status={
          <StatusPill tone="success">
            Connected · {environmentLabel(connection?.environment)}
          </StatusPill>
        }
        subtitle={`Approved returns are signed and sent to the ${centralBankName()} through the AequorOS reporting gateway. Receipts and the regulator's questions come back here. AequorOS manages the connection; there is nothing for you to set up.`}
        action={
          <div className="flex flex-col items-end gap-1.5">
            <button
              type="button"
              disabled={!canConfigure || test.isPending}
              aria-describedby={!canConfigure ? reasonId : undefined}
              onClick={() => test.mutate()}
              className="inline-flex min-h-10 items-center gap-1.5 px-3 text-caption font-medium btn-primary disabled:opacity-60"
            >
              {test.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <PlugZap size={13} aria-hidden />
              )}
              Test connection
            </button>
            <span
              id={reasonId}
              className="max-w-64 text-right text-micro text-slate"
            >
              {canConfigure ? testedLine : configureReason}
            </span>
          </div>
        }
      />

      {connection?.environment === "sandbox" && (
        <p className="rounded-sm border border-warning/30 bg-warning-light/40 px-3.5 py-2.5 text-caption text-navy">
          Sandbox: filings sent here are tests and are not official returns to
          the {centralBankName()}.
        </p>
      )}

      {test.data && (
        <p
          role="status"
          className={`text-caption font-medium ${test.data.ok ? "text-success" : "text-critical"}`}
        >
          {test.data.message}
        </p>
      )}
      {test.error && (
        <ErrorPanel error={test.error} title="Could not test the connection" />
      )}

      <ol
        aria-label="Connection"
        className="card grid grid-cols-1 gap-4 px-5 py-4 sm:grid-cols-2 xl:grid-cols-4"
      >
        {status.steps.map((step) => (
          <li key={step.key} className="flex items-start gap-2.5">
            {step.done ? (
              <CheckCircle2
                size={16}
                className="mt-0.5 shrink-0 text-success"
                aria-hidden
              />
            ) : (
              <Circle
                size={16}
                className="mt-0.5 shrink-0 text-slate"
                aria-hidden
              />
            )}
            <span className="min-w-0">
              <span className="block text-body font-medium text-navy">
                {STEP_LABELS[step.key]}
              </span>
              <span className="block break-words text-caption text-slate">
                {stepDetail(step.key, status)}
              </span>
            </span>
          </li>
        ))}
      </ol>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <SectionCard
          title="Your bank’s signature"
          actions={<StatusPill tone="success">Active</StatusPill>}
        >
          <p className="text-body leading-relaxed text-navy">
            A key that exists only for your bank. It signs a return only after
            your preparer and approver have completed its approvals, so the{" "}
            {centralBankName()} can see each filing came from your bank.
            AequorOS staff cannot use it.
          </p>
          <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Fact label="Key id" value={key?.kid ?? "—"} mono />
            <Fact label="Algorithm" value="ECC P-256 · ES256" />
            <Fact
              label="Created"
              value={
                when(key?.createdAt)
                  ? `${when(key?.createdAt)}, at enrolment`
                  : "At enrolment"
              }
            />
            <Fact
              label="Last used"
              value={when(key?.lastUsedAt) ?? "Not used yet"}
            />
            <Fact
              label="Held by"
              value={
                key?.heldBy === "bank_key_service"
                  ? "Your bank’s own key service"
                  : "AequorOS, encrypted for your bank only"
              }
            />
            <Fact
              label="Signs only when"
              value="Your preparer and approver have both signed off"
            />
          </dl>
        </SectionCard>

        <SectionCard
          title="Connection"
          actions={
            certificate ? (
              <StatusPill tone={certificate.tone}>
                {certificate.label}
              </StatusPill>
            ) : undefined
          }
        >
          <p className="text-body leading-relaxed text-navy">
            {/* Automatic renewal is stated only while AequorOS's scheduled
                check is evidenced for this bank; otherwise it is a staff act. */}
            {connection?.automaticRenewalFrom
              ? `Set up automatically when AequorOS enrolled your bank. AequorOS renews its certificate automatically from ${fmtDateUTC(new Date(connection.automaticRenewalFrom))}, before it expires. There is nothing for you to do.`
              : "Set up automatically when AequorOS enrolled your bank. Its certificate is valid for one year; ask your AequorOS team to renew it before it expires."}
          </p>
          <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Fact
              label="Institution code"
              value={status.institutionCode ?? "—"}
              mono
            />
            <Fact
              label="Gateway"
              value={`AequorOS reporting gateway · ${environmentLabel(connection?.environment).toLowerCase()}`}
            />
            <Fact
              label="Secure connection"
              value={certificate?.detail ?? "—"}
            />
            <Fact
              label="Renewal"
              value={
                connection
                  ? `${
                      connection.automaticRenewalFrom
                        ? `Automatic from ${fmtDateUTC(new Date(connection.automaticRenewalFrom))}`
                        : "By your AequorOS team"
                    }${connection.renewedAt ? ` · last renewed ${fmtDateUTC(new Date(connection.renewedAt))}` : ""}`
                  : "—"
              }
            />
            <Fact
              label="Allowed to"
              value="File returns · read deadlines · answer the regulator’s queries"
            />
          </dl>
        </SectionCard>
      </div>

      <SectionCard
        title="Recent filings through the gateway"
        actions={
          <Link
            href="/submissions/history"
            className="text-caption font-medium text-action hover:underline"
          >
            All filings
          </Link>
        }
        noPadding
      >
        {status.transmissions.length === 0 ? (
          <p className="px-5 py-6 text-body text-slate">
            Nothing has been sent yet. Your first approved return will appear
            here with its gateway receipt.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-body">
              <thead>
                <tr className="border-b border-border-light text-left text-caption text-slate">
                  <th scope="col" className="px-5 py-2.5 font-medium">
                    Return
                  </th>
                  <th scope="col" className="px-3 py-2.5 font-medium">
                    Reporting date
                  </th>
                  <th scope="col" className="px-3 py-2.5 font-medium">
                    Approved by
                  </th>
                  <th scope="col" className="px-3 py-2.5 font-medium">
                    Sent
                  </th>
                  <th scope="col" className="px-3 py-2.5 font-medium">
                    Gateway receipt
                  </th>
                  <th scope="col" className="px-5 py-2.5 font-medium">
                    {regShort()} status
                  </th>
                </tr>
              </thead>
              <tbody>
                {status.transmissions.map((row) => {
                  const state = gatewayState(row.gatewayStatus);
                  return (
                    <tr
                      key={row.receiptId}
                      className="border-b border-border-light last:border-0"
                    >
                      <td className="px-5 py-3 font-medium text-navy">
                        {row.returnCode}
                      </td>
                      <td className="px-3 py-3 text-navy">
                        {fmtDateUTC(row.reportingDate)}
                      </td>
                      <td className="px-3 py-3 text-navy">
                        {row.approvedByRole ?? "Not recorded"}
                      </td>
                      <td className="px-3 py-3 text-navy">
                        {fmtTimestamp(row.sentAt)}
                      </td>
                      <td className="px-3 py-3 font-mono text-caption text-navy">
                        {row.receiptId}
                      </td>
                      <td className="px-5 py-3">
                        <StatusPill tone={state.tone}>{state.label}</StatusPill>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <div className="card flex flex-wrap items-start justify-between gap-4 px-5 py-4">
        <div className="flex min-w-0 items-start gap-3">
          <ShieldCheck
            size={16}
            className="mt-0.5 shrink-0 text-success"
            aria-hidden
          />
          <p className="max-w-3xl text-caption leading-relaxed text-navy">
            <span className="font-medium">
              Nothing is filed without your approvals.
            </span>{" "}
            A return reaches the gateway only after your own preparer and
            approver sign off in AequorOS. To file a return another way, choose
            a different channel when you submit it, or change where approved
            returns go.
          </p>
        </div>
        <Link
          href="/submissions/settings/filing-profile"
          className="text-caption font-medium text-action hover:underline"
        >
          Where returns go
        </Link>
      </div>
    </>
  );
}

function Fact({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-caption text-slate">{label}</dt>
      <dd
        className={`mt-0.5 break-words text-navy ${mono ? "font-mono text-caption" : "text-body font-medium"}`}
      >
        {value}
      </dd>
    </div>
  );
}
