"use client";

import { SettingsHeading } from "@/components/submissions/settings/SettingsChrome";
import SigningPolicyPanel from "@/components/attestation/SigningPolicyPanel";

export default function SigningPoliciesPage() {
  return (
    <>
      <SettingsHeading
        title="Signing policies"
        subtitle="Who must sign which return before it can be filed. Without a policy, every return needs a preparer and an approver."
      />
      <SigningPolicyPanel />
    </>
  );
}
