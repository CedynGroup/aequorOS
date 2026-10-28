import RegulatoryModuleTabs from "@/components/shell/RegulatoryModuleTabs";

const tabs = [
  { href: "/irr", label: "Overview" },
  { href: "/irr/sensitivity", label: "EVE & NII" },
  { href: "/irr/gaps", label: "Gap Analysis" },
  { href: "/irr/scenarios", label: "Scenarios" },
  {
    href: "/irr/enterprise-stress",
    label: "Enterprise stress",
    ruleCode: null, // Cross-module workbench, not the regulatory IRRBB pilot.
  },
  { href: "/irr/limits", label: "Limits" },
  // The prescribed standardised framework. A separate tab, not a mode of the
  // others: it is a different scenario set with its own outlier test, and the
  // legacy engine behind the tabs above is untouched by it.
  {
    href: "/irr/standardised",
    ruleCode: "IRRBB-PILOT",
    label: "Standardised Framework",
  },
];

export default function IrrLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <RegulatoryModuleTabs tabs={tabs} contextRule="IRRBB-PILOT">
        {children}
      </RegulatoryModuleTabs>
    </>
  );
}
