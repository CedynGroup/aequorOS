"use client";

/**
 * The filing profile: what every channel shares, set once, and where the
 * bank's approved returns go. The institution code is read from the
 * institution profile, which is its one home.
 */

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { Loader2, Save } from "lucide-react";
import type {
  ReportingGatewayStatus,
  DefaultChannelCode,
  FilingProfileRead,
  ReportingBasis,
} from "@aequoros/risk-service-api";
import SectionCard from "@/components/ui/SectionCard";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonCard } from "@/components/ui/Skeleton";
import {
  useReportingGatewayStatus,
  useFilingProfile,
  useSaveFilingProfile,
} from "@/lib/api/hooks";
import { useConfigureEligibility, useViewEligibility } from "@/lib/api/configureAccess";
import { centralBankName, regShort } from "@/lib/format";
import { DeniedSettings } from "./DeniedSettings";

const inputClass =
  "w-full min-h-10 rounded-sm border border-border bg-surface-raised px-2.5 py-2 text-body text-navy placeholder:text-slate-light";
const labelClass = "mb-1.5 block text-caption font-medium text-navy";

export const BASIS_LABELS: Record<ReportingBasis, string> = {
  solo: "Solo (licensed entity)",
  consolidated: "Consolidated (banking group)",
};

type Route = {
  value: DefaultChannelCode | "registry";
  label: string;
  detail: string;
  available: boolean;
};

export function defaultChannelLabel(channel: DefaultChannelCode | null | undefined): string {
  switch (channel) {
    case "reporting_gateway":
      return "AequorOS Reporting Gateway";
    case "orass_api":
      return "ORASS";
    case "manual":
      return "Manual record only";
    default:
      return "Each return’s usual channel";
  }
}

function routes(gateway: ReportingGatewayStatus | undefined, orassReady: boolean): Route[] {
  return [
    {
      value: "reporting_gateway",
      label: defaultChannelLabel("reporting_gateway"),
      detail: gateway?.enrolled
        ? `AequorOS’s channel to the ${centralBankName()}. Each filing is signed for your bank and comes back with a signed receipt.`
        : "Available once AequorOS has enrolled your bank on the gateway.",
      available: Boolean(gateway?.enrolled),
    },
    {
      value: "orass_api",
      label: "ORASS",
      detail: orassReady
        ? "Files through ORASS with the access details your bank stored."
        : `Available once the ${centralBankName()} grants your bank API access.`,
      available: orassReady,
    },
    {
      value: "manual",
      label: "Manual record only",
      detail: "File outside AequorOS, then record the filing here.",
      available: true,
    },
    {
      value: "registry",
      label: defaultChannelLabel(null),
      detail: "Each return goes where its template says. Rehearsals use the ORASS simulator.",
      available: true,
    },
  ];
}

export default function FilingProfileForm({ bankId }: { bankId: string }) {
  const { allowed: canView, reason: viewReason } = useViewEligibility(bankId, "reg");
  const profileQuery = useFilingProfile(bankId, { enabled: canView });
  const gatewayQuery = useReportingGatewayStatus(bankId, { enabled: canView });
  const save = useSaveFilingProfile(bankId);
  const { allowed: canConfigure, reason: configureReason } = useConfigureEligibility(
    bankId,
    "reg",
  );
  const reasonId = useId();
  const fieldId = useId();
  const [form, setForm] = useState<{
    reportingBasis: ReportingBasis;
    contactName: string;
    contactEmail: string;
    defaultChannel: DefaultChannelCode | "registry";
  } | null>(null);

  const profile = profileQuery.data;
  useEffect(() => {
    if (form || !profile) return;
    setForm({
      reportingBasis: profile.reportingBasis,
      contactName: profile.contactName ?? "",
      contactEmail: profile.contactEmail ?? "",
      defaultChannel: profile.defaultChannel ?? "registry",
    });
  }, [profile, form]);

  if (!canView) return <DeniedSettings title="Filing profile" reason={viewReason} />;
  if (profileQuery.error) {
    return (
      <ErrorPanel
        error={profileQuery.error}
        onRetry={() => profileQuery.refetch()}
        title="Could not load the filing profile"
      />
    );
  }
  if (!profile || !form) return <SkeletonCard />;

  // The profile says whether ORASS API access is set up, so the page never asks for
  // a channel configuration that does not exist (a 404 the browser logs as an error).
  const options = routes(gatewayQuery.data, profile.orassReady ?? false);
  const id = (name: string) => `${fieldId}-${name}`;

  const handleSave = () => {
    if (!canConfigure) return;
    save.mutate({
      reportingBasis: form.reportingBasis,
      contactName: form.contactName.trim() || null,
      contactEmail: form.contactEmail.trim() || null,
      defaultChannel: form.defaultChannel === "registry" ? null : form.defaultChannel,
    });
  };

  return (
    <div className="space-y-6">
      <SectionCard
        title="Filing profile"
        subtitle="Used by every channel, so you set it once."
        actions={
          <span className="inline-flex max-w-64 flex-wrap items-center justify-end gap-2">
            {!canConfigure && (
              <span id={reasonId} className="text-right text-micro text-slate">
                {configureReason}
              </span>
            )}
            <button
              type="button"
              disabled={!canConfigure || save.isPending}
              aria-describedby={!canConfigure ? reasonId : undefined}
              onClick={handleSave}
              className="inline-flex min-h-10 items-center gap-1.5 px-3 text-caption font-medium btn-primary disabled:opacity-60"
            >
              {save.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <Save size={13} aria-hidden />
              )}
              Save
            </button>
          </span>
        }
      >
        <div className="grid max-w-3xl grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <p className={labelClass}>{regShort()} institution code</p>
            <p className="min-h-10 py-2 font-mono text-body text-navy">
              {profile.institutionCode ?? "Not recorded"}
            </p>
            <p className="mt-1 text-micro leading-relaxed text-slate">
              Kept in your{" "}
              <Link href="/institution" className="text-action hover:underline">
                institution profile
              </Link>
              .
            </p>
          </div>
          <div>
            <label className={labelClass} htmlFor={id("basis")}>
              Default reporting basis
            </label>
            <select
              id={id("basis")}
              value={form.reportingBasis}
              onChange={(e) =>
                setForm({ ...form, reportingBasis: e.target.value as ReportingBasis })
              }
              className={inputClass}
            >
              {(Object.keys(BASIS_LABELS) as ReportingBasis[]).map((basis) => (
                <option key={basis} value={basis}>
                  {BASIS_LABELS[basis]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={labelClass} htmlFor={id("contact")}>
              Reporting contact
            </label>
            <input
              id={id("contact")}
              value={form.contactName}
              onChange={(e) => setForm({ ...form, contactName: e.target.value })}
              placeholder="Name and title"
              className={inputClass}
            />
          </div>
          <div>
            <label className={labelClass} htmlFor={id("email")}>
              Contact email
            </label>
            <input
              id={id("email")}
              type="email"
              value={form.contactEmail}
              onChange={(e) => setForm({ ...form, contactEmail: e.target.value })}
              placeholder="regreporting@yourbank"
              className={inputClass}
            />
          </div>
        </div>
      </SectionCard>

      <SectionCard
        title="Where approved returns go"
        subtitle="The channel a return uses once its approvals are complete. You can still choose another for a single filing."
      >
        <fieldset className="grid max-w-4xl grid-cols-1 gap-3 md:grid-cols-2">
          <legend className="sr-only">Default channel</legend>
          {options.map((option) => {
            const checked = form.defaultChannel === option.value;
            return (
              <label
                key={option.value}
                className={`flex cursor-pointer flex-col gap-1.5 rounded-sm border px-4 py-3 ${
                  checked
                    ? "border-action bg-action-light"
                    : "border-border bg-surface-raised"
                } ${option.available ? "" : "cursor-not-allowed opacity-70"}`}
              >
                <span className="flex items-center gap-2.5">
                  <input
                    type="radio"
                    name={id("route")}
                    value={option.value}
                    checked={checked}
                    disabled={!option.available}
                    onChange={() => setForm({ ...form, defaultChannel: option.value })}
                    className="h-4 w-4 accent-[rgb(var(--accent))]"
                  />
                  <span className="text-body font-medium text-navy">{option.label}</span>
                </span>
                <span className="pl-7 text-caption leading-relaxed text-slate">
                  {option.detail}
                </span>
              </label>
            );
          })}
        </fieldset>
        <p className="mt-4 max-w-3xl text-caption leading-relaxed text-slate">
          Returns the regulator receives by email, and returns recorded manually,
          keep their own route. If the gateway is unavailable, file by email
          fallback, then re-send through the gateway when it recovers.
        </p>
      </SectionCard>

      {save.error && <ErrorPanel error={save.error} title="Could not save" />}
      {save.isSuccess && (
        <p role="status" className="text-caption font-medium text-success">
          Filing profile saved.
        </p>
      )}
    </div>
  );
}

export function profileFacts(profile: FilingProfileRead): { label: string; value: string; mono?: boolean }[] {
  return [
    { label: `${regShort()} institution code`, value: profile.institutionCode ?? "Not recorded", mono: true },
    { label: "Default reporting basis", value: BASIS_LABELS[profile.reportingBasis] },
    { label: "Reporting contact", value: profile.contactName ?? "Not recorded" },
    { label: "Contact email", value: profile.contactEmail ?? "Not recorded" },
  ];
}
