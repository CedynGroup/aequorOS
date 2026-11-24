"use client";

import { useBankContext } from "@/components/shell/BankContext";
import { SettingsHeading } from "@/components/submissions/settings/SettingsChrome";
import FilingProfileForm from "@/components/submissions/settings/FilingProfileForm";

export default function FilingProfilePage() {
  const { bank } = useBankContext();
  return (
    <>
      <SettingsHeading
        title="Filing profile"
        subtitle="Who your bank is to the regulator, and where its approved returns go."
      />
      {bank && <FilingProfileForm bankId={bank.id} />}
    </>
  );
}
