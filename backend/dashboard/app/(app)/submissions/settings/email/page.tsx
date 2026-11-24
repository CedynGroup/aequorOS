"use client";

import { useBankContext } from "@/components/shell/BankContext";
import { SettingsHeading } from "@/components/submissions/settings/SettingsChrome";
import ChannelForm from "@/components/submissions/settings/ChannelForm";

export default function EmailFallbackPage() {
  const { bank } = useBankContext();
  return (
    <>
      <SettingsHeading
        title="Email fallback"
        section="Submission channels"
        subtitle="When the regulator’s system is down, AequorOS produces the complete files and step-by-step instructions to email them, then reminds you to re-send once it recovers."
      />
      {bank && <ChannelForm bankId={bank.id} channel="email" />}
    </>
  );
}
