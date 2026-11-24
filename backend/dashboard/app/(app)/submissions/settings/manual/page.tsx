"use client";

import { SettingsHeading } from "@/components/submissions/settings/SettingsChrome";
import SectionCard from "@/components/ui/SectionCard";

export default function ManualRecordPage() {
  return (
    <>
      <SettingsHeading
        title="Manual record"
        section="Submission channels"
        subtitle="Records a filing made outside AequorOS so the history stays complete."
      />
      <SectionCard title="How it works">
        <ul className="max-w-3xl list-disc space-y-2 pl-5 text-body leading-relaxed text-navy">
          <li>
            File the return on the regulator’s portal or by hand, as you normally
            would.
          </li>
          <li>
            In the return, choose <span className="font-medium">Manual record</span>{" "}
            and enter the regulator’s reference, such as the portal receipt number.
          </li>
          <li>
            AequorOS records exactly the files that already existed when you filed,
            with the approvals behind them. Nothing new is produced.
          </li>
        </ul>
        <p className="mt-4 text-caption text-slate">
          There is nothing to configure for this channel.
        </p>
      </SectionCard>
    </>
  );
}
