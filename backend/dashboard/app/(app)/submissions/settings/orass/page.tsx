"use client";

import { AlertTriangle } from "lucide-react";
import { useBankContext } from "@/components/shell/BankContext";
import { SettingsHeading } from "@/components/submissions/settings/SettingsChrome";
import ChannelForm from "@/components/submissions/settings/ChannelForm";
import { DeniedSettings } from "@/components/submissions/settings/DeniedSettings";
import StatusPill from "@/components/ui/StatusPill";
import { isChannelConfigMissingError, useChannelConfig } from "@/lib/api/hooks";
import { useViewEligibility } from "@/lib/api/configureAccess";
import { centralBankName, regShort } from "@/lib/format";

export default function OrassPage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;
  const { allowed: canView, reason: viewReason } = useViewEligibility(bankId, "reg");
  const access = useChannelConfig(bankId, "orass_api", {
    enabled: Boolean(bankId) && canView,
  });
  const stored = access.data?.config as Record<string, unknown> | undefined;
  const connected =
    !isChannelConfigMissingError(access.error) && Boolean(stored?.api_base_url);
  const known = canView && !access.isPending;

  return (
    <>
      <SettingsHeading
        title="ORASS"
        section="Submission channels"
        status={
          known ? (
            connected ? (
              <StatusPill tone="success">Access details stored</StatusPill>
            ) : (
              <StatusPill tone="amber">Awaiting access</StatusPill>
            )
          ) : undefined
        }
      />
      {known && !connected && (
        <div className="flex items-start gap-3 rounded-sm border border-warning/30 bg-warning-light/40 px-4 py-3">
          <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warning" aria-hidden />
          <p className="max-w-3xl text-caption leading-relaxed text-navy">
            <span className="font-medium">Your bank can’t file through ORASS yet.</span>{" "}
            The {centralBankName()} has not issued ORASS API access for AequorOS. When
            it does, enter the access details below and ORASS becomes available as a
            channel. Until then, file through the {regShort()} reporting gateway.
          </p>
        </div>
      )}
      {!canView ? (
        <DeniedSettings title="ORASS" reason={viewReason} />
      ) : (
        bankId && (
          <>
            <ChannelForm bankId={bankId} channel="orass_api" />
            <ChannelForm bankId={bankId} channel="orass_sandbox" />
          </>
        )
      )}
    </>
  );
}
