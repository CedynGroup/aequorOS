"use client";

/**
 * Regulatory Reporting → Settings: how this bank files. One surface for the
 * filing profile every channel shares, where approved returns go, the state of
 * each submission channel, signing and deadlines. Each section links to its
 * own page; nothing here edits configuration directly.
 */

import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowRight, Mail, NotebookPen, RadioTower, Server } from "lucide-react";
import { useBankContext } from "@/components/shell/BankContext";
import { SettingsHeading } from "@/components/submissions/settings/SettingsChrome";
import { DeniedSettings } from "@/components/submissions/settings/DeniedSettings";
import {
  defaultChannelLabel,
  profileFacts,
} from "@/components/submissions/settings/FilingProfileForm";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill, { type StatusTone } from "@/components/ui/StatusPill";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonCard } from "@/components/ui/Skeleton";
import {
  isChannelConfigMissingError,
  useReportingGatewayStatus,
  useChannelConfig,
  useFilingProfile,
  useReportingSettings,
  useSigningPolicies,
} from "@/lib/api/hooks";
import {
  useOrganizationViewEligibility,
  useViewEligibility,
} from "@/lib/api/configureAccess";
import { fmtTimestamp } from "@/lib/api/values";
import { centralBankName, regShort } from "@/lib/format";

const BASE = "/submissions/settings";

type ChannelRow = {
  label: string;
  status: { label: string; tone: StatusTone };
  detail: string;
  href: string;
  icon: ReactNode;
};

export default function ReportingSettingsOverview() {
  const { bank } = useBankContext();
  const bankId = bank?.id;
  const { allowed: canView, reason: viewReason } = useViewEligibility(bankId, "reg");
  const profileQuery = useFilingProfile(bankId, { enabled: canView });
  const gatewayQuery = useReportingGatewayStatus(bankId, { enabled: canView });
  const orassQuery = useChannelConfig(bankId, "orass_api", { enabled: canView });
  const emailQuery = useChannelConfig(bankId, "email", { enabled: canView });
  const deadlinesQuery = useReportingSettings(bankId, { enabled: canView });
  const { allowed: canViewPolicies } = useOrganizationViewEligibility("reg");
  const policiesQuery = useSigningPolicies(canViewPolicies);

  const heading = (
    <SettingsHeading
      title={bank ? `How ${bank.shortName ?? bank.name} files` : "How your bank files"}
      subtitle="Who you are to the regulator, how your returns reach it, and who must sign them."
    />
  );
  if (!canView) {
    return (
      <>
        {heading}
        <DeniedSettings title="Reporting settings" reason={viewReason} />
      </>
    );
  }
  if (profileQuery.error) {
    return (
      <>
        {heading}
        <ErrorPanel
          error={profileQuery.error}
          onRetry={() => profileQuery.refetch()}
          title="Could not load your reporting settings"
        />
      </>
    );
  }
  const profile = profileQuery.data;
  if (!profile) {
    return (
      <>
        {heading}
        <SkeletonCard />
      </>
    );
  }

  const gateway = gatewayQuery.data;
  const latest = gateway?.transmissions[0];
  const orassConfig = orassQuery.data?.config as Record<string, unknown> | undefined;
  const orassReady =
    !isChannelConfigMissingError(orassQuery.error) && Boolean(orassConfig?.api_base_url);
  const emailConfig = emailQuery.data?.config as Record<string, unknown> | undefined;
  const emailReady = Boolean(emailConfig?.fallback_recipient);
  const overrides = Object.keys(deadlinesQuery.data?.deadlineOverrides ?? {}).length;
  const policies = policiesQuery.data?.policies.length;
  const signingOff = policiesQuery.data?.signingSuspendedDeploymentWide === true;

  const channels: ChannelRow[] = [
    {
      label: "AequorOS Reporting Gateway",
      status: gateway?.enrolled
        ? { label: "Connected", tone: "success" }
        : { label: "Not yet enrolled", tone: "slate" },
      detail: gateway?.enrolled
        ? [
            "Enrolled by AequorOS",
            latest ? `last filing ${fmtTimestamp(latest.sentAt)}` : "no filings yet",
            "nothing to manage",
          ].join(" · ")
        : "Your AequorOS team enrols your bank; there is nothing for you to set up.",
      href: `${BASE}/reporting-gateway`,
      icon: <RadioTower size={18} aria-hidden />,
    },
    {
      label: "ORASS",
      status: orassReady
        ? { label: "Access details stored", tone: "success" }
        : { label: "Awaiting access", tone: "amber" },
      detail: orassReady
        ? "Files through ORASS with your stored access details."
        : `The ${centralBankName()} has not yet issued API access. Rehearsals run against a clearly labelled simulator.`,
      href: `${BASE}/orass`,
      icon: <Server size={18} aria-hidden />,
    },
    {
      label: "Email fallback",
      status: emailReady
        ? { label: "Ready", tone: "action" }
        : { label: "No address", tone: "slate" },
      detail:
        "For downtime only: produces the complete files and step-by-step instructions to email them.",
      href: `${BASE}/email`,
      icon: <Mail size={18} aria-hidden />,
    },
    {
      label: "Manual record",
      status: { label: "Ready", tone: "action" },
      detail: "Records a filing made outside AequorOS so the history stays complete.",
      href: `${BASE}/manual`,
      icon: <NotebookPen size={18} aria-hidden />,
    },
  ];

  return (
    <>
      {heading}

      <SectionCard
        title="Filing profile"
        subtitle="Used by every channel, so you set it once."
        actions={
          <Link
            href={`${BASE}/filing-profile`}
            className="inline-flex min-h-10 items-center rounded-sm border border-border px-3 text-caption font-medium text-navy hover:bg-surface-raised"
          >
            Edit profile
          </Link>
        }
      >
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {profileFacts(profile).map((fact) => (
            <div key={fact.label} className="min-w-0">
              <dt className="text-caption text-slate">{fact.label}</dt>
              <dd
                className={`mt-0.5 break-words text-navy ${fact.mono ? "font-mono text-body" : "text-body font-medium"}`}
              >
                {fact.value}
              </dd>
            </div>
          ))}
        </dl>
      </SectionCard>

      <SectionCard
        title="Where approved returns go"
        subtitle="The channel a return uses once its approvals are complete. You can still choose another for a single filing."
        actions={
          <Link
            href={`${BASE}/filing-profile`}
            className="text-caption font-medium text-action hover:underline"
          >
            Change
          </Link>
        }
      >
        <p className="text-body font-medium text-navy">
          {defaultChannelLabel(profile.defaultChannel)}
        </p>
        <p className="mt-1 max-w-3xl text-caption leading-relaxed text-slate">
          If the gateway is unavailable, file by email fallback, then re-send through
          the gateway when it recovers.
        </p>
      </SectionCard>

      <SectionCard title="Submission channels" noPadding>
        <ul>
          {channels.map((channel) => (
            <li
              key={channel.href}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-border-light px-5 py-4 last:border-0"
            >
              <div className="flex min-w-0 items-start gap-3">
                <span className="mt-0.5 text-action">{channel.icon}</span>
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="text-body font-medium text-navy">{channel.label}</span>
                    <StatusPill tone={channel.status.tone}>{channel.status.label}</StatusPill>
                  </p>
                  <p className="mt-0.5 text-caption leading-relaxed text-slate">
                    {channel.detail}
                  </p>
                </div>
              </div>
              <Link
                href={channel.href}
                className="inline-flex min-h-10 items-center gap-1 text-caption font-medium text-action hover:underline"
              >
                Manage
                <ArrowRight size={13} aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      </SectionCard>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <SectionCard
          title="Signing"
          actions={
            policiesQuery.data ? (
              <StatusPill tone={signingOff ? "slate" : "success"}>
                {signingOff ? "Switched off" : "On"}
              </StatusPill>
            ) : undefined
          }
        >
          <p className="text-body leading-relaxed text-navy">
            {policies === undefined
              ? "Who must sign which return before it can be filed."
              : signingOff
                ? `Returns follow the prepare-and-approve route. ${
                    policies === 0
                      ? "Policies you record take"
                      : `Your ${policies} recorded ${policies === 1 ? "policy takes" : "policies take"}`
                  } effect when signing is switched on.`
                : policies === 0
                  ? "No policies recorded. Every return needs a preparer and an approver."
                  : `${policies} ${policies === 1 ? "policy" : "policies"} recorded.`}
          </p>
          <Link
            href={`${BASE}/signing`}
            className="mt-3 inline-block text-caption font-medium text-action hover:underline"
          >
            Manage signing policies
          </Link>
        </SectionCard>
        <SectionCard title="Deadlines">
          <p className="text-body leading-relaxed text-navy">
            {overrides === 0
              ? "No extensions. Reporting dates and deadlines follow the regulator’s schedule for each return."
              : `${overrides} agreed ${overrides === 1 ? "extension" : "extensions"}. Every other return follows the regulator’s schedule.`}
          </p>
          <Link
            href={`${BASE}/deadlines`}
            className="mt-3 inline-block text-caption font-medium text-action hover:underline"
          >
            {overrides === 0 ? "Add an agreed extension" : "Manage extensions"}
          </Link>
        </SectionCard>
      </div>
    </>
  );
}
